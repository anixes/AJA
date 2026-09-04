"""
verification_runner.py - Standalone verification engine for AJA.
================================================================
Provides autonomous validation checks (AST syntax checking and command execution)
inspired by OpenCode 2's closed-loop verification gate.
"""

from __future__ import annotations

import ast
import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence


@dataclass
class VerificationResult:
    """Outcome of an autonomous verification check."""

    passed: bool
    check_type: str
    summary: str
    details: Optional[str] = None
    exit_code: int = 0

    def to_feedback_prompt(self) -> str:
        """Format the failure into a concise instruction for the model to self-correct."""
        if self.passed:
            return ""
        lines = [
            f"[Autonomous Verification Failure: {self.check_type}]",
            f"Summary: {self.summary}",
        ]
        if self.exit_code != 0:
            lines.append(f"Exit code: {self.exit_code}")
        if self.details:
            lines.append(f"Details:\n{self.details.strip()}")
        lines.append("Please analyze the error, repair the code, and ensure all checks pass.")
        return "\n".join(lines)


def verify_python_syntax(files: Sequence[Path | str]) -> VerificationResult:
    """Verify that all given Python files are syntactically valid via AST parsing."""
    errors: List[str] = []
    checked_count = 0

    for file_item in files:
        path = Path(file_item)
        if not path.is_file() or path.suffix != ".py":
            continue

        checked_count += 1
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            ast.parse(content, filename=str(path))
        except SyntaxError as e:
            err_desc = f"- {path.name}:{e.lineno}:{e.offset}: {e.msg}"
            if e.text:
                err_desc += f"\n    Line: {e.text.strip()}"
            errors.append(err_desc)
        except Exception as e:
            errors.append(f"- {path.name}: Failed to read/parse: {e}")

    if errors:
        return VerificationResult(
            passed=False,
            check_type="python_syntax",
            summary=f"{len(errors)} file(s) contain syntax errors.",
            details="\n".join(errors),
            exit_code=1,
        )

    return VerificationResult(
        passed=True,
        check_type="python_syntax",
        summary=f"All {checked_count} Python file(s) passed syntax validation.",
        exit_code=0,
    )


async def run_command_verifier(
    cmd: str,
    cwd: Optional[Path | str] = None,
    timeout: int = 60,
) -> VerificationResult:
    """Execute a verification command (e.g. pytest, ruff, py_compile) and capture output."""
    working_dir = str(cwd) if cwd else os.getcwd()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return VerificationResult(
                passed=False,
                check_type="command",
                summary=f"Verification command timed out after {timeout}s: '{cmd}'",
                details=f"Command exceeded timeout limit of {timeout} seconds.",
                exit_code=-1,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode or 0

        if exit_code == 0:
            return VerificationResult(
                passed=True,
                check_type="command",
                summary=f"Verification command succeeded: '{cmd}'",
                details=stdout[-500:] if len(stdout) > 500 else stdout,
                exit_code=0,
            )

        # Failure output aggregation
        out_snippets = []
        if stderr:
            out_snippets.append(f"STDERR:\n{stderr[-1500:]}")
        if stdout:
            out_snippets.append(f"STDOUT:\n{stdout[-1500:]}")
        details = "\n".join(out_snippets) if out_snippets else "No output produced."

        return VerificationResult(
            passed=False,
            check_type="command",
            summary=f"Verification command failed with exit code {exit_code}: '{cmd}'",
            details=details,
            exit_code=exit_code,
        )
    except Exception as e:
        return VerificationResult(
            passed=False,
            check_type="command",
            summary=f"Failed to execute verification command '{cmd}': {e}",
            details=str(e),
            exit_code=127,
        )
