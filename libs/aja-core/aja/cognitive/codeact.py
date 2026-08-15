"""
AJA Cognitive Engine: CodeAct Unified Action Executor
Implements Executable Code Actions (ICML 2024 CodeAct paradigm).
Enables the agent to formulate multi-step logic as executable Python or Bash code blocks.
"""

import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CodeActResult:
    """Result of a CodeAct execution step."""
    language: str
    code: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    status: str  # 'success', 'error', 'timeout'

    @property
    def output(self) -> str:
        if self.stdout and self.stderr:
            return f"STDOUT:\n{self.stdout}\nSTDERR:\n{self.stderr}"
        return self.stdout or self.stderr or "(no output)"

    @property
    def success(self) -> bool:
        return self.status == "success" and self.exit_code == 0


class CodeActExecutor:
    """
    Executes Python and Shell code blocks with timeout safeguards,
    ambient working directory resolution, and stdout/stderr capture.
    """

    def __init__(self, default_timeout_seconds: float = 60.0, **kwargs):
        self.default_timeout = kwargs.get("default_timeout_s", kwargs.get("default_timeout", default_timeout_seconds))

    @staticmethod
    def extract_code(raw_text: str) -> tuple[str, str]:
        """
        Extracts language and code body from the first markdown code block or plain text.
        Returns (language, code).
        """
        match = re.search(
            r"(?:^|\n)```([a-zA-Z0-9_\-]+)?\s*\n(.*?)\n```(?:\n|$)",
            raw_text,
            re.DOTALL,
        )
        if match:
            lang = (match.group(1) or "python").lower()
            code = match.group(2).strip()
            if lang in {"bash", "sh", "shell", "pwsh", "powershell"}:
                lang = "shell"
            else:
                lang = "python"
            return lang, code

        # Fallback to loose search if no strict fence found
        match_loose = re.search(r"```([a-zA-Z0-9_\-]+)?\s*\n(.*?)```", raw_text, re.DOTALL)
        if match_loose:
            lang = (match_loose.group(1) or "python").lower()
            code = match_loose.group(2).strip()
            if lang in {"bash", "sh", "shell", "pwsh", "powershell"}:
                lang = "shell"
            else:
                lang = "python"
            return lang, code

        return "python", raw_text.strip()

    @staticmethod
    def extract_code_blocks(raw_text: str) -> list[tuple[str, str]]:
        """
        Extracts all markdown code blocks in order.
        Returns list of (language, code).
        """
        blocks = []
        matches = list(re.finditer(
            r"(?:^|\n)```([a-zA-Z0-9_\-]+)?\s*\n(.*?)\n```(?:\n|$)",
            raw_text,
            re.DOTALL,
        ))
        if not matches:
            matches = list(re.finditer(r"```([a-zA-Z0-9_\-]+)?\s*\n(.*?)```", raw_text, re.DOTALL))

        for match in matches:
            raw_lang = (match.group(1) or "python").lower()
            code = match.group(2).strip()
            if raw_lang in {"bash", "sh", "shell", "pwsh", "powershell"}:
                lang = "bash"
            else:
                lang = "python"
            blocks.append((lang, code))
        return blocks

    def execute_python(self, code: str, cwd: Optional[Path] = None, timeout: Optional[float] = None) -> CodeActResult:
        """Executes Python code directly with timeout trapping."""
        return self.execute(code, language="python", cwd=cwd, timeout=timeout)

    def execute_shell(self, code: str, cwd: Optional[Path] = None, timeout: Optional[float] = None) -> CodeActResult:
        """Executes Shell / Bash commands directly with timeout trapping."""
        return self.execute(code, language="shell", cwd=cwd, timeout=timeout)

    def execute(
        self,
        code_or_block: str,
        language: Optional[str] = None,
        cwd: Optional[Path] = None,
        timeout: Optional[float] = None,
    ) -> CodeActResult:
        """
        Executes a Python or Shell code block and captures the outcome.
        """
        extracted_lang, code = self.extract_code(code_or_block)
        lang = (language or extracted_lang).lower()
        timeout_sec = timeout if timeout is not None else self.default_timeout
        exec_cwd = str((cwd or Path.home()).resolve())

        start_time = time.perf_counter()

        if lang == "python":
            return self._execute_python(code, exec_cwd, timeout_sec, start_time)
        else:
            return self._execute_shell(code, exec_cwd, timeout_sec, start_time)

    def _execute_python(self, code: str, cwd: str, timeout: float, start_time: float) -> CodeActResult:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            temp_path = f.name

        try:
            cmd = [sys.executable, temp_path]
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy(),
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            status = "success" if proc.returncode == 0 else "error"
            return CodeActResult(
                language="python",
                code=code,
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                status=status,
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return CodeActResult(
                language="python",
                code=code,
                stdout="",
                stderr=f"Execution timed out after {timeout} seconds.",
                exit_code=124,
                duration_ms=duration_ms,
                status="timeout",
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return CodeActResult(
                language="python",
                code=code,
                stdout="",
                stderr=str(e),
                exit_code=1,
                duration_ms=duration_ms,
                status="error",
            )
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def _execute_shell(self, code: str, cwd: str, timeout: float, start_time: float) -> CodeActResult:
        shell_cmd = code
        is_windows = os.name == "nt"

        try:
            proc = subprocess.run(
                shell_cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy(),
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            status = "success" if proc.returncode == 0 else "error"
            return CodeActResult(
                language="shell",
                code=code,
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                status=status,
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return CodeActResult(
                language="shell",
                code=code,
                stdout="",
                stderr=f"Shell execution timed out after {timeout} seconds.",
                exit_code=124,
                duration_ms=duration_ms,
                status="timeout",
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return CodeActResult(
                language="shell",
                code=code,
                stdout="",
                stderr=str(e),
                exit_code=1,
                duration_ms=duration_ms,
                status="error",
            )
