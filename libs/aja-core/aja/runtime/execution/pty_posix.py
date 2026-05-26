from __future__ import annotations

import asyncio
import os
import pty
import sys
from typing import Optional
from aja.runtime.execution.transport import ExecutionTransport


class POSIXPTYTransport(ExecutionTransport):
    """
    Event-loop-native pseudo-terminal execution transport for POSIX platforms.
    Utilizes standard library pty module, os.setsid, and non-blocking fd reading
    directly on the asyncio loop to avoid background threads.
    """

    def __init__(
        self,
        command: list[str] | str,
        cwd: str,
        env: dict[str, str],
        shell: bool = True,
    ):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.env = env
        self.shell = shell
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._loop = asyncio.get_running_loop()

    async def start(self) -> None:
        self.master_fd, self.slave_fd = pty.openpty()
        os.set_inheritable(self.slave_fd, True)

        self.stdout = asyncio.StreamReader()
        self.stderr = None  # PTY multiplexes stderr into stdout

        def preexec():
            os.setsid()
            # Set controlling terminal to slave_fd
            try:
                import fcntl
                import termios
                fcntl.ioctl(self.slave_fd, termios.TIOCSCTTY, 0)
            except Exception:
                pass

        cmd_args = self.command
        if self.shell:
            cmd_str = self.command if isinstance(self.command, str) else " ".join(self.command)
            cmd_args = ["/bin/sh", "-c", cmd_str]
        elif isinstance(cmd_args, str):
            cmd_args = [cmd_args]

        self._proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=self.cwd,
            env=self.env,
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            preexec_fn=preexec,
        )

        self.pid = self._proc.pid

        # Close the slave FD in the parent process immediately to avoid hanging
        os.close(self.slave_fd)
        self.slave_fd = None

        # Set master_fd non-blocking
        os.set_blocking(self.master_fd, False)

        # Register non-blocking read listener on active event loop
        self._loop.add_reader(self.master_fd, self._handle_fd_read)

        class PTYWriter:
            def __init__(self, master_fd: int):
                self.master_fd = master_fd

            def write(self, data: bytes):
                try:
                    os.write(self.master_fd, data)
                except Exception:
                    pass

            async def drain(self):
                pass

            def close(self):
                pass

        self.stdin = PTYWriter(self.master_fd)

    def _handle_fd_read(self) -> None:
        try:
            data = os.read(self.master_fd, 8192)
            if data:
                self.stdout.feed_data(data)
            else:
                self._stop_reading()
        except BlockingIOError:
            pass
        except Exception:
            self._stop_reading()

    def _stop_reading(self) -> None:
        if self.master_fd is not None:
            try:
                self._loop.remove_reader(self.master_fd)
            except Exception:
                pass
            try:
                os.close(self.master_fd)
            except Exception:
                pass
            self.master_fd = None
        self.stdout.feed_eof()

    async def wait(self) -> int:
        if not self._proc:
            raise RuntimeError("Transport has not been started.")
        self.returncode = await self._proc.wait()
        self._stop_reading()
        return self.returncode

    def terminate(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
        self._stop_reading()

    def kill(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        self._stop_reading()
