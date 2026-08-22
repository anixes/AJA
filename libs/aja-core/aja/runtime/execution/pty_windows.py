import asyncio
import os
import threading
from typing import Optional
from aja.runtime.execution.transport import ExecutionTransport

try:
    import pywinpty
except ImportError:
    try:
        # The distribution is "pywinpty" but the import name is "winpty".
        import winpty as pywinpty
    except ImportError:
        pywinpty = None


class WindowsPTYTransport(ExecutionTransport):
    """
    Async wrapper around pywinpty subclassing ExecutionTransport.
    Provides true ConPTY streaming semantics on Windows, enabling
    interactive programs, full ANSI color preservation, and cooperative thread cleanup.
    """

    def __init__(self, command: list[str] | str, cwd: str, env: dict):
        super().__init__()
        if pywinpty is None:
            raise RuntimeError("pywinpty is required for Windows PTY execution.")
        
        self.cmd = command if isinstance(command, str) else " ".join(command)
        self.cwd = cwd
        self.env = env
        self.pty = pywinpty.PTY(80, 24)
        
        self.stdout = asyncio.StreamReader()
        self.stderr = None  # PTY multiplexes stderr into stdout
        
        self._exited = asyncio.Event()
        self._read_task = None
        self._poll_task = None
        self._cancelled = False
        # Guards pty.read() vs pty.close() overlap: pywinpty is not
        # thread-safe for a blocked native read concurrent with close.
        self._io_lock = threading.Lock()
        # Guards the one-shot transition to "closed" so double-close is safe.
        self._close_lock = threading.Lock()
        self._closed = False

    async def start(self) -> None:
        # pywinpty 3.x: spawn(appname, cmdline=None, cwd=None, env=None) —
        # appname is positional-required; the full command line is accepted.
        self.pty.spawn(
            self.cmd,
            cwd=self.cwd,
            env=self.env
        )
        self.pid = self.pty.pid
        
        self._read_task = asyncio.create_task(self._read_loop())
        self._poll_task = asyncio.create_task(self._poll_loop())
        
        class PTYWriter:
            def __init__(self, pty):
                self.pty = pty
            def write(self, data: bytes):
                try:
                    self.pty.write(data.decode('utf-8'))
                except Exception:
                    pass
            async def drain(self):
                pass
            def close(self):
                pass
        
        self.stdin = PTYWriter(self.pty)

    async def _read_loop(self) -> None:
        while not self._cancelled and not self._closed:
            try:
                # Non-blocking native read (blocking=True would park the
                # threadpool worker forever after child exit, since ConPTY
                # keeps the output handle open until close()).
                data = await asyncio.to_thread(self._safe_pty_read)
                if not data:
                    if not getattr(self, 'pty', None) or not self.pty.isalive() or self._cancelled or self._closed:
                        break
                    await asyncio.sleep(0.05)
                    continue
                self.stdout.feed_data(data.encode('utf-8'))
            except Exception:
                break
        self.stdout.feed_eof()

    def _safe_pty_read(self) -> Optional[str]:
        # Serialize against _cleanup_native: if cleanup already closed or is
        # closing the handle, do not enter the native read at all.
        with self._io_lock:
            if self._cancelled or self._closed or not getattr(self, 'pty', None):
                return None
            try:
                return self.pty.read(4096, False)
            except Exception:
                return None

    async def _poll_loop(self) -> None:
        while getattr(self, 'pty', None) and self.pty.isalive() and not self._cancelled and not self._closed:
            await asyncio.sleep(0.1)
        
        if not self._cancelled and not self._closed and getattr(self, 'pty', None):
            try:
                exit_code = self.pty.get_exitstatus()
                self.returncode = -1 if exit_code is None else exit_code
            except Exception:
                self.returncode = 1
        else:
            self.returncode = -1
            
        self._exited.set()
        self._teardown()

    def _cleanup_native(self) -> None:
        """Force-close the native ConPTY handle BEFORE touching the read loop.

        A reader thread parked inside the blocking ``pty.read(4096, True)``
        call cannot be interrupted by task cancellation; only closing the
        underlying ConPTY handle makes that native read error out promptly.
        Cancelling tasks first (the old ordering) therefore left readers
        wedged forever. Idempotent via ``_close_lock``.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._cancelled = True

        pty = getattr(self, 'pty', None)
        self.pty = None
        if pty is not None:
            # Prefer serialized close; if a native read is already parked we
            # still force the close after a short grace period — that is what
            # unblocks it. Never wait indefinitely for the io lock here.
            locked = self._io_lock.acquire(timeout=0.5)
            try:
                try:
                    pty.close()
                except Exception:
                    pass
            finally:
                if locked:
                    self._io_lock.release()

        # Only now cancel/wait on the loops: with the handle gone, any blocked
        # native read has already errored out and the tasks can wind down.
        for task in (self._read_task, self._poll_task):
            if task is not None and not task.done():
                task.cancel()

    # Alias kept for callers of the previous name.
    def _teardown(self) -> None:
        self._cleanup_native()

    def stop(self) -> None:
        self._cleanup_native()
        self._exited.set()

    def close(self) -> None:
        self.stop()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode or 0

    def terminate(self) -> None:
        self.stop()

    def kill(self) -> None:
        self.terminate()
