import asyncio
import os
from typing import Optional

try:
    import pywinpty
except ImportError:
    pywinpty = None


class WindowsPTYProcess:
    """
    Async wrapper around pywinpty to mimic asyncio.subprocess.Process.
    This provides true ConPTY streaming semantics on Windows, enabling
    interactive programs and full ANSI color preservation.
    """

    def __init__(self, cmd: str, cwd: str, env: dict):
        if pywinpty is None:
            raise RuntimeError("pywinpty is required for Windows PTY execution.")
        
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.pty = pywinpty.PTY(80, 24)
        
        self.stdout = asyncio.StreamReader()
        self.stderr = None  # PTY multiplexes stderr into stdout
        
        self.pid: Optional[int] = None
        self.returncode: Optional[int] = None
        self._exited = asyncio.Event()
        self._read_task = None
        self._poll_task = None

    def start(self):
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

    async def _read_loop(self):
        while True:
            try:
                # pywinpty read is blocking, we use to_thread to prevent event loop stalls
                data = await asyncio.to_thread(self.pty.read, 4096, True)
                if not data:
                    if not self.pty.isalive():
                        break
                    await asyncio.sleep(0.01)
                    continue
                self.stdout.feed_data(data.encode('utf-8'))
            except Exception:
                break
        self.stdout.feed_eof()

    async def _poll_loop(self):
        while self.pty.isalive():
            await asyncio.sleep(0.1)
        
        try:
            exit_code = self.pty.get_exitstatus()
            # None typically means 0 or it exited cleanly in some versions
            self.returncode = 0 if exit_code is None else exit_code
        except Exception:
            self.returncode = 1
            
        self._exited.set()
        
        try:
            self.pty.close()
        except Exception:
            pass

    async def wait(self):
        await self._exited.wait()
        return self.returncode

    def terminate(self):
        try:
            self.pty.close()
        except Exception:
            pass

    def kill(self):
        self.terminate()
