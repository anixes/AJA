from __future__ import annotations

import abc
import asyncio
import os
import sys
from typing import Optional


class ExecutionTransport(abc.ABC):
    """Abstract Base Class defining the unified process stream execution transport."""

    def __init__(self):
        self.pid: Optional[int] = None
        self.returncode: Optional[int] = None
        self.stdin: Optional[any] = None
        self.stdout: Optional[asyncio.StreamReader] = None
        self.stderr: Optional[asyncio.StreamReader] = None

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialize the transport and spawn the underlying process."""
        pass

    @abc.abstractmethod
    async def wait(self) -> int:
        """Wait for the process to exit and return the code."""
        pass

    @abc.abstractmethod
    def terminate(self) -> None:
        """Gracefully terminate the process."""
        pass

    @abc.abstractmethod
    def kill(self) -> None:
        """Forcefully kill the process."""
        pass


class PipeTransport(ExecutionTransport):
    """Concrete ExecutionTransport implementation wrapping standard non-PTY system subprocesses."""

    def __init__(
        self,
        command: list[str] | str,
        cwd: str,
        env: dict[str, str],
        shell: bool = True,
        preexec_fn: Optional[callable] = None,
        creationflags: int = 0,
    ):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.env = env
        self.shell = shell
        
        # POSIX Orphan Prevention
        if preexec_fn is None and sys.platform != 'win32':
            self.preexec_fn = os.setsid
        else:
            self.preexec_fn = preexec_fn
            
        self.creationflags = creationflags
        self._proc: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        if self.shell:
            cmd_str = self.command if isinstance(self.command, str) else " ".join(self.command)
            self._proc = await asyncio.create_subprocess_shell(
                cmd_str,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=self.creationflags,
                preexec_fn=self.preexec_fn,
            )
        else:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=self.creationflags,
                preexec_fn=self.preexec_fn,
            )

        self.pid = self._proc.pid
        self.stdin = self._proc.stdin
        self.stdout = self._proc.stdout
        self.stderr = self._proc.stderr

    async def wait(self) -> int:
        if not self._proc:
            raise RuntimeError("Transport has not been started.")
        self.returncode = await self._proc.wait()
        return self.returncode

    def terminate(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass

    def kill(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
