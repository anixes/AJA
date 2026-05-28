import asyncio
import os
from typing import Optional
from aja.runtime.execution.transport import ExecutionTransport

try:
    import pywinpty
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

    async def start(self) -> None:
        self.pty.spawn(
            app_name=None,
            cmdline=self.cmd,
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
        while not self._cancelled:
            try:
                # pywinpty read is blocking, we use to_thread to prevent event loop stalls
                data = await asyncio.to_thread(self._safe_pty_read)
                if not data:
                    if not getattr(self, 'pty', None) or not self.pty.isalive() or self._cancelled:
                        break
                    await asyncio.sleep(0.01)
                    continue
                self.stdout.feed_data(data.encode('utf-8'))
            except Exception:
                break
        self.stdout.feed_eof()

    def _safe_pty_read(self) -> Optional[str]:
        if self._cancelled or not getattr(self, 'pty', None):
            return None
        try:
            return self.pty.read(4096, True)
        except Exception:
            return None

    async def _poll_loop(self) -> None:
        while getattr(self, 'pty', None) and self.pty.isalive() and not self._cancelled:
            await asyncio.sleep(0.1)
        
        if not self._cancelled and getattr(self, 'pty', None):
            try:
                exit_code = self.pty.get_exitstatus()
                self.returncode = -1 if exit_code is None else exit_code
            except Exception:
                self.returncode = 1
        else:
            self.returncode = -1
            
        self._exited.set()
        self._cleanup_native()

    def _cleanup_native(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        
        if getattr(self, '_read_task', None) and not self._read_task.done():
            self._read_task.cancel()
        if getattr(self, '_poll_task', None) and not self._poll_task.done():
            self._poll_task.cancel()

        try:
            if getattr(self, 'pty', None):
                self.pty.close()
        finally:
            self.pty = None

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode or 0

    def terminate(self) -> None:
        self._cleanup_native()
        self._exited.set()

    def kill(self) -> None:
        self.terminate()
