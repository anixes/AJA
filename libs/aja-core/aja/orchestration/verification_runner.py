"""
verification_runner.py - Standalone deterministic verification engine for AJA.
================================================================================
Provides autonomous validation checks:
- Tier 1: AST Python syntax validation & workspace hygiene (< 100ms)
- Tier 2: Targeted test execution with environment isolation
- Tier 3: Git workspace and diff sanity check (wipeout and empty-diff guards)
- Composite Verification Ladder: Fast-failing multi-tier verification runner.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple


def smart_truncate_output(
    text: str,
    max_head_lines: int = 25,
    max_tail_lines: int = 35,
    max_bytes: int = 4000,
) -> str:
    """
    Truncate diagnostic output preserving both the root cause (head) and final traceback (tail).
    Handles edge cases: empty strings, short text, and respects unicode character boundaries
    when enforcing max_bytes.
    """
    if not text:
        return ""

    lines = text.splitlines()
    total_lines = len(lines)

    if total_lines <= max_head_lines + max_tail_lines:
        result = "\n".join(lines)
    else:
        head = lines[:max_head_lines]
        tail = lines[-max_tail_lines:]
        omitted = total_lines - (max_head_lines + max_tail_lines)
        result = "\n".join(head + [f"[... truncated {omitted} lines ...]"] + tail)

    # Enforce hard byte cap on unicode character boundaries
    encoded = result.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        trimmed = encoded[:max_bytes].decode("utf-8", errors="ignore")
        result = trimmed.rstrip() + "\n[... truncated by byte limit ...]"

    return result


@dataclass
class VerificationResult:
    """Outcome of an autonomous verification check."""

    # Positional fields preserved for 100% backward compatibility:
    passed: bool
    check_type: str = "generic"
    summary: str = ""
    details: Optional[str] = None
    exit_code: int = 0

    # Additive fields with defaults:
    tier: Optional[int] = None
    duration_ms: float = 0.0
    command: Optional[str] = None
    cwd: Optional[str] = None
    skipped_tiers: List[int] = field(default_factory=list)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    oscillation_count: int = 0

    def to_feedback_prompt(self, oscillation_count: int = 0) -> str:
        """Format the failure into a concise instruction for the model to self-correct."""
        if self.passed:
            return ""

        lines = [
            f"[Autonomous Verification Failure: {self.check_type}]",
        ]
        if self.tier is not None:
            lines.append(f"Tier: {self.tier}")
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        elif self.error_message:
            lines.append(f"Summary: {self.error_message}")
        if self.exit_code != 0:
            lines.append(f"Exit code: {self.exit_code}")
        if self.command:
            lines.append(f"Command: {self.command}")
        if self.cwd:
            lines.append(f"Working Dir: {Path(self.cwd).as_posix()}")
        if self.skipped_tiers:
            lines.append(f"Skipped Tiers: {self.skipped_tiers}")
        if self.details:
            lines.append(f"Details:\n```\n{self.details.strip()}\n```")
        lines.append("Please analyze the error, repair the code, and ensure all checks pass.")

        effective_oscillation = max(oscillation_count, self.oscillation_count)
        if effective_oscillation >= 2:
            lines.append(
                f"\n[Warning: Repeated Failure Detected ({effective_oscillation} consecutive identical failures)]\n"
                "The same verification error has repeated without resolution.\n"
                "Do NOT re-apply the exact same change. Step back, re-evaluate assumptions, inspect related dependencies, or adopt an alternative approach."
            )

        return "\n".join(lines)


def verify_file_syntax(path: Path) -> Tuple[bool, Optional[str]]:
    """
    Inspect a single file for syntax correctness and structural hygiene.
    Supports .py, .json, .toml, .yaml, .yml.
    Flags null-byte binary corruption and zero-byte empty files.
    """
    if not path.is_file():
        return True, None

    suffix = path.suffix.lower()
    known_suffixes = {".py", ".json", ".toml", ".yaml", ".yml"}
    if suffix not in known_suffixes:
        return True, None

    try:
        raw_bytes = path.read_bytes()
    except Exception as e:
        return False, f"- {path.name}: Failed to read file: {e}"

    # 1. Null-byte binary corruption check
    if b"\x00" in raw_bytes:
        return False, f"- {path.name}: File contains null bytes (binary corruption detected)"

    # 2. Zero-byte code/config file check
    if len(raw_bytes) == 0:
        return False, f"- {path.name}: Zero-byte empty code or configuration file detected"

    try:
        content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, f"- {path.name}: UTF-8 decode error: {e}"

    # 3. Format-specific syntax parsing
    if suffix == ".py":
        try:
            ast.parse(content, filename=str(path))
        except SyntaxError as e:
            err_desc = f"- {path.name}:{e.lineno}:{e.offset}: {e.msg}"
            if e.text:
                err_desc += f"\n    Line: {e.text.strip()}"
            return False, err_desc
        except Exception as e:
            return False, f"- {path.name}: Failed to parse Python AST: {e}"

    elif suffix == ".json":
        try:
            import json
            json.loads(content)
        except json.JSONDecodeError as e:
            return False, f"- {path.name}:{e.lineno}:{e.colno}: JSON syntax error: {e.msg}"
        except Exception as e:
            return False, f"- {path.name}: Failed to parse JSON: {e}"

    elif suffix == ".toml":
        try:
            import tomllib
            tomllib.loads(content)
        except ImportError:
            try:
                import tomli
                tomli.loads(content)
            except ImportError:
                if content.count('"') % 2 != 0:
                    return False, f"- {path.name}: TOML syntax error: Unclosed quote in TOML file"
        except Exception as e:
            return False, f"- {path.name}: TOML syntax error: {e}"

    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
            yaml.safe_load(content)
        except ImportError:
            if content.count("{") != content.count("}") or content.count("[") != content.count("]"):
                return False, f"- {path.name}: YAML syntax error: Unbalanced braces or brackets in YAML file"
        except Exception as e:
            return False, f"- {path.name}: YAML syntax error: {e}"

    return True, None


def verify_syntax(
    files: Sequence[Path | str],
    cwd: Optional[Path | str] = None,
) -> VerificationResult:
    """Verify that given files are syntactically valid and pass structural hygiene."""
    t0 = time.perf_counter()
    errors: List[str] = []
    checked_count = 0
    base_dir = Path(cwd) if cwd else Path.cwd()

    for file_item in files:
        raw_path = Path(file_item)
        path = raw_path if raw_path.is_absolute() else base_dir / raw_path
        if not path.is_file():
            continue

        checked_count += 1
        passed, err = verify_file_syntax(path)
        if not passed and err:
            errors.append(err)

    duration_ms = (time.perf_counter() - t0) * 1000.0

    all_py = all(Path(f).suffix.lower() == ".py" for f in files) if files else True
    file_type_str = "Python " if all_py else ""

    if errors:
        visible_errors = errors[:10]
        if len(errors) > 10:
            visible_errors.append(f"... and {len(errors) - 10} more syntax error(s).")

        return VerificationResult(
            passed=False,
            check_type="syntax_and_hygiene",
            summary=f"{len(errors)} file(s) contain syntax errors.",
            details="\n".join(visible_errors),
            exit_code=1,
            tier=1,
            duration_ms=duration_ms,
            cwd=str(base_dir),
            error_type="syntax_error",
        )

    return VerificationResult(
        passed=True,
        check_type="syntax_and_hygiene",
        summary=f"All {checked_count} {file_type_str}file(s) passed syntax validation.",
        exit_code=0,
        tier=1,
        duration_ms=duration_ms,
        cwd=str(base_dir),
    )


def verify_python_syntax(
    files: Sequence[Path | str],
    cwd: Optional[Path | str] = None,
) -> VerificationResult:
    """Backward-compatible alias for verify_syntax."""
    py_files = [f for f in files if str(f).endswith(".py")]
    res = verify_syntax(py_files if py_files else files, cwd=cwd)
    res.check_type = "python_syntax"
    return res


def verify_syntax_in_workspace(
    root: Path | str = ".",
    max_files: int = 150,
    exclude_dirs: Optional[Sequence[Path | str]] = None,
) -> VerificationResult:
    """
    Discover and validate Python/JSON/TOML/YAML syntax across workspace with deterministic ordering.
    Safely skips virtualenvs, git, cache, and node_modules. If files exceed max_files, discloses partial coverage.
    """
    t0 = time.perf_counter()
    base_dir = Path(root).resolve()
    raw_excludes = exclude_dirs or (
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        "build",
        "dist",
        ".tox",
        ".mypy_cache",
        "node_modules",
    )
    normalized_excludes: Set[str] = {str(Path(e).name).lower() for e in raw_excludes}

    all_files: List[Path] = []
    target_exts = {".py", ".json", ".toml", ".yaml", ".yml"}
    try:
        for p in base_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in target_exts:
                parts_lower = {part.lower() for part in p.parts}
                if not parts_lower.intersection(normalized_excludes):
                    all_files.append(p)
    except Exception as e:
        return VerificationResult(
            passed=False,
            check_type="syntax_and_hygiene",
            summary=f"Failed to scan workspace: {e}",
            details=str(e),
            exit_code=1,
            tier=1,
            cwd=str(base_dir),
            error_type="scan_error",
        )

    all_files.sort(key=lambda p: str(p))
    total_count = len(all_files)
    candidate_files = all_files[:max_files]
    is_partial = total_count > max_files
    all_py = all(p.suffix.lower() == ".py" for p in all_files) if all_files else True
    file_type_str = "Python " if all_py else ""

    res = verify_syntax(candidate_files, cwd=base_dir)
    res.duration_ms = (time.perf_counter() - t0) * 1000.0

    if res.passed:
        if is_partial:
            res.summary = (
                f"Checked first {max_files} of {total_count} {file_type_str}files "
                "(partial coverage); all passed syntax validation."
            )
        else:
            res.summary = f"All {total_count} {file_type_str}file(s) passed syntax validation."

    return res


async def run_command_verifier(
    cmd: str,
    cwd: Optional[Path | str] = None,
    timeout: int = 60,
) -> VerificationResult:
    """Execute a verification command and capture output (backward-compatible)."""
    t0 = time.perf_counter()
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
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                command=cmd,
                cwd=working_dir,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode or 0
        duration_ms = (time.perf_counter() - t0) * 1000.0

        if exit_code == 0:
            return VerificationResult(
                passed=True,
                check_type="command",
                summary=f"Verification command succeeded: '{cmd}'",
                details=stdout[-500:] if len(stdout) > 500 else stdout,
                exit_code=0,
                duration_ms=duration_ms,
                command=cmd,
                cwd=working_dir,
            )

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
            duration_ms=duration_ms,
            command=cmd,
            cwd=working_dir,
        )
    except Exception as e:
        return VerificationResult(
            passed=False,
            check_type="command",
            summary=f"Failed to execute verification command '{cmd}': {e}",
            details=str(e),
            exit_code=127,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            command=cmd,
            cwd=working_dir,
        )


def discover_test_targets(
    files: Sequence[Path | str],
    cwd: Optional[Path | str] = None,
    workspace_root: Optional[Path | str] = None,
    max_targets: int = 10,
) -> List[str]:
    """
    Safely discover candidate test files corresponding to modified source files.
    Confines all searches within workspace_root.
    """
    base_dir = Path(workspace_root or cwd or Path.cwd()).resolve()
    tests_dir = base_dir / "tests"
    if not tests_dir.exists() or not tests_dir.is_dir():
        return []

    discovered: Set[str] = set()

    for file_item in files:
        raw_p = Path(file_item)
        try:
            p = raw_p.resolve() if raw_p.is_absolute() else (base_dir / raw_p).resolve()
            # Workspace path confinement: ignore files outside workspace root
            if not p.is_relative_to(base_dir):
                continue
        except Exception:
            continue

        # If modified file is already a test file
        if p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts:
            try:
                if p.is_file() and p.is_relative_to(base_dir):
                    discovered.add(p.relative_to(base_dir).as_posix())
                    continue
            except Exception:
                pass

        if p.suffix != ".py":
            continue

        stem = p.stem
        # Direct candidates
        candidate_rel_paths = [
            f"tests/test_{stem}.py",
            f"tests/python/unit/test_{stem}.py",
            f"tests/unit/test_{stem}.py",
            f"tests/integration/test_{stem}.py",
            f"tests/planning/test_{stem}.py",
            f"tests/{stem}_test.py",
        ]
        found_direct = False
        for c in candidate_rel_paths:
            candidate_full = base_dir / c
            if candidate_full.is_file():
                discovered.add(c)
                found_direct = True

        if not found_direct:
            # Bounded search in tests/ for any test containing the stem
            try:
                for match in tests_dir.rglob(f"test_*{stem}*.py"):
                    if match.is_file() and match.is_relative_to(base_dir):
                        discovered.add(match.relative_to(base_dir).as_posix())
                        if len(discovered) >= max_targets:
                            break
            except Exception:
                pass

    return sorted(list(discovered))[:max_targets]


def verify_deliverables(
    deliverables: Sequence[Path | str],
    cwd: Optional[Path | str] = None,
) -> VerificationResult:
    """Verify that claimed deliverables exist on disk and are non-empty."""
    t0 = time.perf_counter()
    base_dir = Path(cwd) if cwd else Path.cwd()
    missing: List[str] = []
    empty: List[str] = []

    for d in deliverables:
        raw_p = Path(d)
        p = raw_p if raw_p.is_absolute() else base_dir / raw_p
        if not p.exists():
            missing.append(str(raw_p.as_posix()))
        elif p.is_file() and p.stat().st_size == 0:
            empty.append(str(raw_p.as_posix()))

    duration_ms = (time.perf_counter() - t0) * 1000.0

    if missing:
        return VerificationResult(
            passed=False,
            check_type="deliverables",
            summary=f"Deliverable verification failed: {len(missing)} file(s) missing on disk.",
            details="Missing deliverable(s):\n" + "\n".join(f"- {m}" for m in missing),
            exit_code=1,
            tier=3,
            duration_ms=duration_ms,
            cwd=str(base_dir),
            error_type="missing_deliverables",
        )

    if empty:
        return VerificationResult(
            passed=False,
            check_type="deliverables",
            summary=f"Deliverable verification failed: {len(empty)} file(s) are 0 bytes (empty).",
            details="Empty deliverable(s):\n" + "\n".join(f"- {e}" for e in empty),
            exit_code=1,
            tier=3,
            duration_ms=duration_ms,
            cwd=str(base_dir),
            error_type="empty_deliverables",
        )

    return VerificationResult(
        passed=True,
        check_type="deliverables",
        summary=f"All {len(deliverables)} deliverable(s) verified on disk.",
        exit_code=0,
        tier=3,
        duration_ms=duration_ms,
        cwd=str(base_dir),
    )


def _build_test_argv(test_target: Optional[str]) -> List[str]:
    """Build argv list for test runner without shell=True for cross-platform safety."""
    if not test_target or not test_target.strip():
        return [sys.executable, "-m", "pytest"]

    clean = test_target.strip()
    if clean.startswith("pytest"):
        parts = clean.split()[1:]
        return [sys.executable, "-m", "pytest"] + parts
    if clean.endswith(".py") or "::" in clean:
        clean_target = clean.strip('"\'')
        return [sys.executable, "-m", "pytest", clean_target]

    if sys.platform == "win32":
        return [a.strip('"\'') for a in shlex.split(clean, posix=False)]
    return shlex.split(clean)


async def run_targeted_tests(
    test_target: Optional[str] = None,
    cwd: Optional[Path | str] = None,
    timeout: int = 60,
    env: Optional[dict] = None,
) -> VerificationResult:
    """Execute targeted test suite with environment isolation and timeout enforcement."""
    t0 = time.perf_counter()
    working_dir = str(cwd) if cwd else os.getcwd()
    argv = _build_test_argv(test_target)
    merged_env = {**os.environ, **(env or {})}
    cmd_str = " ".join(argv)

    spawn_kwargs = {}
    if sys.platform != "win32":
        spawn_kwargs["start_new_session"] = True

    try:
        proc = await asyncio.create_subprocess_exec(
            argv[0],
            *argv[1:],
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            **spawn_kwargs,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            if sys.platform == "win32":
                try:
                    tk_proc = await asyncio.create_subprocess_exec(
                        "taskkill", "/F", "/T", "/PID", str(proc.pid),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(tk_proc.wait(), timeout=5.0)
                except Exception:
                    pass
            else:
                try:
                    import signal
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return VerificationResult(
                passed=False,
                check_type="targeted_test",
                summary=f"Verification command timed out after {timeout}s",
                details=f"Test run exceeded timeout limit of {timeout} seconds.",
                exit_code=-1,
                tier=2,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                command=cmd_str,
                cwd=working_dir,
                error_type="timeout",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode or 0
        duration_ms = (time.perf_counter() - t0) * 1000.0

        combined = f"{stdout}\n{stderr}".strip()
        if "No module named pytest" in combined:
            return VerificationResult(
                passed=False,
                check_type="targeted_test",
                summary="pytest not available in environment",
                details=smart_truncate_output(combined),
                exit_code=127,
                tier=2,
                duration_ms=duration_ms,
                command=cmd_str,
                cwd=working_dir,
                error_type="missing_runner",
            )

        if exit_code == 0:
            return VerificationResult(
                passed=True,
                check_type="targeted_test",
                summary=f"Targeted test suite passed: {cmd_str}",
                details=smart_truncate_output(stdout, max_head_lines=10, max_tail_lines=15),
                exit_code=0,
                tier=2,
                duration_ms=duration_ms,
                command=cmd_str,
                cwd=working_dir,
            )

        # Pytest exit code 5: No tests collected (not a code failure)
        if exit_code == 5:
            return VerificationResult(
                passed=True,
                check_type="targeted_test",
                summary=f"Targeted test run completed (exit code 5: no tests collected): {cmd_str}",
                details=smart_truncate_output(stdout or stderr or "Pytest collected 0 tests.", max_head_lines=10, max_tail_lines=15),
                exit_code=5,
                tier=2,
                duration_ms=duration_ms,
                command=cmd_str,
                cwd=working_dir,
                error_type="no_tests_collected",
            )

        return VerificationResult(
            passed=False,
            check_type="targeted_test",
            summary=f"Targeted test suite failed with exit code {exit_code}",
            details=smart_truncate_output(combined),
            exit_code=exit_code,
            tier=2,
            duration_ms=duration_ms,
            command=cmd_str,
            cwd=working_dir,
            error_type="test_failure",
        )
    except FileNotFoundError as e:
        return VerificationResult(
            passed=False,
            check_type="targeted_test",
            summary=f"Test runner executable not found: {argv[0]}",
            details=str(e),
            exit_code=127,
            tier=2,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            command=cmd_str,
            cwd=working_dir,
            error_type="not_found",
        )
    except Exception as e:
        return VerificationResult(
            passed=False,
            check_type="targeted_test",
            summary=f"Failed to execute targeted tests: {e}",
            details=str(e),
            exit_code=1,
            tier=2,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            command=cmd_str,
            cwd=working_dir,
            error_type="execution_error",
        )


def run_targeted_tests_sync(
    test_target: Optional[str] = None,
    cwd: Optional[Path | str] = None,
    timeout: int = 60,
    env: Optional[dict] = None,
) -> VerificationResult:
    """Synchronous targeted test execution using subprocess.run."""
    t0 = time.perf_counter()
    working_dir = str(cwd) if cwd else os.getcwd()
    argv = _build_test_argv(test_target)
    merged_env = {**os.environ, **(env or {})}
    cmd_str = " ".join(argv)

    try:
        proc = subprocess.run(
            argv,
            cwd=working_dir,
            capture_output=True,
            timeout=timeout,
            env=merged_env,
        )
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode
        duration_ms = (time.perf_counter() - t0) * 1000.0
        combined = f"{stdout}\n{stderr}".strip()

        if "No module named pytest" in combined:
            return VerificationResult(
                passed=False,
                check_type="targeted_test",
                summary="pytest not available in environment",
                details=smart_truncate_output(combined),
                exit_code=127,
                tier=2,
                duration_ms=duration_ms,
                command=cmd_str,
                cwd=working_dir,
                error_type="missing_runner",
            )

        if exit_code == 0:
            return VerificationResult(
                passed=True,
                check_type="targeted_test",
                summary=f"Targeted test suite passed: {cmd_str}",
                details=smart_truncate_output(stdout, max_head_lines=10, max_tail_lines=15),
                exit_code=0,
                tier=2,
                duration_ms=duration_ms,
                command=cmd_str,
                cwd=working_dir,
            )

        # Pytest exit code 5: No tests collected (not a code failure)
        if exit_code == 5:
            return VerificationResult(
                passed=True,
                check_type="targeted_test",
                summary=f"Targeted test run completed (exit code 5: no tests collected): {cmd_str}",
                details=smart_truncate_output(stdout or stderr or "Pytest collected 0 tests.", max_head_lines=10, max_tail_lines=15),
                exit_code=5,
                tier=2,
                duration_ms=duration_ms,
                command=cmd_str,
                cwd=working_dir,
                error_type="no_tests_collected",
            )

        return VerificationResult(
            passed=False,
            check_type="targeted_test",
            summary=f"Targeted test suite failed with exit code {exit_code}",
            details=smart_truncate_output(combined),
            exit_code=exit_code,
            tier=2,
            duration_ms=duration_ms,
            command=cmd_str,
            cwd=working_dir,
            error_type="test_failure",
        )
    except subprocess.TimeoutExpired:
        return VerificationResult(
            passed=False,
            check_type="targeted_test",
            summary=f"Verification command timed out after {timeout}s",
            details=f"Test run exceeded timeout limit of {timeout} seconds.",
            exit_code=-1,
            tier=2,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            command=cmd_str,
            cwd=working_dir,
            error_type="timeout",
        )
    except FileNotFoundError as e:
        return VerificationResult(
            passed=False,
            check_type="targeted_test",
            summary=f"Test runner executable not found: {argv[0]}",
            details=str(e),
            exit_code=127,
            tier=2,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            command=cmd_str,
            cwd=working_dir,
            error_type="not_found",
        )
    except Exception as e:
        return VerificationResult(
            passed=False,
            check_type="targeted_test",
            summary=f"Failed to execute targeted tests: {e}",
            details=str(e),
            exit_code=1,
            tier=2,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            command=cmd_str,
            cwd=working_dir,
            error_type="execution_error",
        )


def verify_git_diff(
    cwd: Optional[Path | str] = None,
    allow_empty: bool = False,
    max_deletions: int = 500,
    ignore_whitespace: bool = True,
) -> VerificationResult:
    """
    Tier 3: Validate workspace git diff for empty modifications and destructive wipeouts.
    Properly detects newly created untracked files to avoid false-positive empty diffs.
    """
    t0 = time.perf_counter()
    working_dir = str(cwd) if cwd else os.getcwd()

    try:
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=working_dir,
            capture_output=True,
            timeout=15,
        )
        if status_proc.returncode != 0:
            return VerificationResult(
                passed=True,
                check_type="git_diff",
                summary="Git check skipped: not a git repository",
                exit_code=0,
                tier=3,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                cwd=working_dir,
            )
        status_lines = status_proc.stdout.decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        return VerificationResult(
            passed=True,
            check_type="git_diff",
            summary=f"Git check skipped: {e}",
            exit_code=0,
            tier=3,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            cwd=working_dir,
        )

    diff_cmd = ["git", "diff", "HEAD", "--numstat"]
    if ignore_whitespace:
        diff_cmd.append("-w")

    try:
        numstat_proc = subprocess.run(
            diff_cmd,
            cwd=working_dir,
            capture_output=True,
            timeout=15,
        )
        numstat_lines = numstat_proc.stdout.decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        return VerificationResult(
            passed=False,
            check_type="git_diff",
            summary=f"Failed to inspect git diff: {e}",
            details=str(e),
            exit_code=1,
            tier=3,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            cwd=working_dir,
        )

    total_added = 0
    total_deleted = 0
    file_deletions: List[Tuple[int, str]] = []

    for line in numstat_lines:
        parts = line.strip().split("\t")
        if len(parts) >= 3:
            add_str, del_str, fname = parts[0], parts[1], parts[2]
            added = int(add_str) if add_str.isdigit() else 0
            deleted = int(del_str) if del_str.isdigit() else 0
            total_added += added
            total_deleted += deleted
            if deleted > 0:
                file_deletions.append((deleted, fname))

    has_untracked = any(line.strip().startswith("??") for line in status_lines)
    has_tracked_changes = len(numstat_lines) > 0 or any(
        not line.strip().startswith("??") for line in status_lines
    )

    if not allow_empty and not has_untracked and not has_tracked_changes:
        return VerificationResult(
            passed=False,
            check_type="git_diff",
            summary="Workspace verification failed: no modifications or new files detected.",
            details="Workspace has no git changes or untracked files.",
            exit_code=1,
            tier=3,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            cwd=working_dir,
        )

    if (
        total_deleted > 50
        and total_deleted > max_deletions
        and total_added < (total_deleted / 5)
    ):
        file_deletions.sort(key=lambda x: x[0], reverse=True)
        top_files = [f"- {fname}: {count} lines deleted" for count, fname in file_deletions[:5]]
        return VerificationResult(
            passed=False,
            check_type="git_diff",
            summary=f"Suspicious massive deletion: {total_deleted} lines deleted vs {total_added} lines added.",
            details="Top files by deletions:\n" + "\n".join(top_files),
            exit_code=1,
            tier=3,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            cwd=working_dir,
        )

    return VerificationResult(
        passed=True,
        check_type="git_diff",
        summary=f"Git workspace sanity verified (+{total_added}, -{total_deleted}).",
        details="\n".join(status_lines[:15]) if status_lines else "Clean working directory.",
        exit_code=0,
        tier=3,
        duration_ms=(time.perf_counter() - t0) * 1000.0,
        cwd=working_dir,
    )


async def run_verification_ladder(
    *,
    files_to_check: Optional[Sequence[Path | str]] = None,
    test_target: Optional[str] = None,
    check_git: bool = True,
    allow_empty_diff: bool = False,
    cwd: Optional[Path | str] = None,
    timeout: int = 60,
    env: Optional[dict] = None,
    auto_discover_tests: bool = True,
    task_type: str = "MODIFY",
    claimed_deliverables: Optional[Sequence[str]] = None,
    tier1_enabled: bool = True,
    tier2_enabled: bool = True,
    tier3_enabled: bool = True,
    max_deletions: int = 500,
    max_files_scan: int = 150,
) -> VerificationResult:
    """
    Execute the Deterministic Verification Ladder in hierarchical order:
    Tier 1: Syntax & Hygiene -> Tier 2: Targeted Tests -> Tier 3: Git Diff Sanity.
    Short-circuits immediately on failure with skipped_tiers populated.
    """
    t0 = time.perf_counter()
    base_dir = Path(cwd) if cwd else Path.cwd()
    skipped_tiers: List[int] = []

    # Tier 1: Syntax & structural hygiene check
    if tier1_enabled:
        if files_to_check is not None:
            res1 = verify_syntax(files_to_check, cwd=base_dir)
        else:
            res1 = verify_syntax_in_workspace(root=base_dir, max_files=max_files_scan)

        if not res1.passed:
            skipped = []
            if tier2_enabled and (test_target is not None or (auto_discover_tests and files_to_check)):
                skipped.append(2)
            if tier3_enabled and check_git:
                skipped.append(3)
            return VerificationResult(
                passed=False,
                check_type="ladder",
                summary=f"Tier 1 (Syntax) failed: {res1.summary}",
                details=res1.details,
                exit_code=res1.exit_code,
                tier=1,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                cwd=str(base_dir),
                skipped_tiers=skipped,
                error_type="tier1_syntax",
            )
    else:
        skipped_tiers.append(1)

    # Tier 2: Targeted tests (explicit target or auto-discovered)
    effective_test_target = test_target
    if tier2_enabled:
        if effective_test_target is None and auto_discover_tests and files_to_check:
            discovered = discover_test_targets(files_to_check, cwd=base_dir)
            if discovered:
                effective_test_target = " ".join(discovered)

        if effective_test_target is not None:
            res2 = await run_targeted_tests(
                test_target=effective_test_target, cwd=base_dir, timeout=timeout, env=env
            )
            if not res2.passed:
                skipped = [3] if (tier3_enabled and check_git) else []
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    summary=f"Tier 2 (Test) failed: {res2.summary}",
                    details=res2.details,
                    exit_code=res2.exit_code,
                    tier=2,
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                    command=res2.command,
                    cwd=str(base_dir),
                    skipped_tiers=skipped,
                    error_type="tier2_test",
                )
        else:
            skipped_tiers.append(2)
    else:
        skipped_tiers.append(2)

    # Tier 3: Git sanity & Deliverable integrity
    if tier3_enabled:
        if check_git:
            effective_allow_empty = allow_empty_diff or (task_type.upper() == "READ_ONLY")
            res3 = verify_git_diff(cwd=base_dir, allow_empty=effective_allow_empty, max_deletions=max_deletions)
            if not res3.passed:
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    summary=f"Tier 3 (Git) failed: {res3.summary}",
                    details=res3.details,
                    exit_code=res3.exit_code,
                    tier=3,
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                    cwd=str(base_dir),
                    skipped_tiers=skipped_tiers,
                    error_type="tier3_git",
                )

        if claimed_deliverables:
            res_deliv = verify_deliverables(claimed_deliverables, cwd=base_dir)
            if not res_deliv.passed:
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    summary=f"Tier 3 (Deliverables) failed: {res_deliv.summary}",
                    details=res_deliv.details,
                    exit_code=res_deliv.exit_code,
                    tier=3,
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                    cwd=str(base_dir),
                    skipped_tiers=skipped_tiers,
                    error_type="tier3_deliverables",
                )
    else:
        skipped_tiers.append(3)

    return VerificationResult(
        passed=True,
        check_type="ladder",
        summary="All active verification ladder tiers passed",
        exit_code=0,
        tier=None,
        duration_ms=(time.perf_counter() - t0) * 1000.0,
        cwd=str(base_dir),
        skipped_tiers=skipped_tiers,
    )


def run_verification_ladder_sync(
    *,
    files_to_check: Optional[Sequence[Path | str]] = None,
    test_target: Optional[str] = None,
    check_git: bool = True,
    allow_empty_diff: bool = False,
    cwd: Optional[Path | str] = None,
    timeout: int = 60,
    env: Optional[dict] = None,
    auto_discover_tests: bool = True,
    task_type: str = "MODIFY",
    claimed_deliverables: Optional[Sequence[str]] = None,
    tier1_enabled: bool = True,
    tier2_enabled: bool = True,
    tier3_enabled: bool = True,
    max_deletions: int = 500,
    max_files_scan: int = 150,
) -> VerificationResult:
    """Synchronous entry point for the Deterministic Verification Ladder."""
    t0 = time.perf_counter()
    base_dir = Path(cwd) if cwd else Path.cwd()
    skipped_tiers: List[int] = []

    # Tier 1
    if tier1_enabled:
        if files_to_check is not None:
            res1 = verify_syntax(files_to_check, cwd=base_dir)
        else:
            res1 = verify_syntax_in_workspace(root=base_dir, max_files=max_files_scan)

        if not res1.passed:
            skipped = []
            if tier2_enabled and (test_target is not None or (auto_discover_tests and files_to_check)):
                skipped.append(2)
            if tier3_enabled and check_git:
                skipped.append(3)
            return VerificationResult(
                passed=False,
                check_type="ladder",
                summary=f"Tier 1 (Syntax) failed: {res1.summary}",
                details=res1.details,
                exit_code=res1.exit_code,
                tier=1,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                cwd=str(base_dir),
                skipped_tiers=skipped,
                error_type="tier1_syntax",
            )
    else:
        skipped_tiers.append(1)

    # Tier 2
    effective_test_target = test_target
    if tier2_enabled:
        if effective_test_target is None and auto_discover_tests and files_to_check:
            discovered = discover_test_targets(files_to_check, cwd=base_dir)
            if discovered:
                effective_test_target = " ".join(discovered)

        if effective_test_target is not None:
            res2 = run_targeted_tests_sync(
                test_target=effective_test_target, cwd=base_dir, timeout=timeout, env=env
            )
            if not res2.passed:
                skipped = [3] if (tier3_enabled and check_git) else []
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    summary=f"Tier 2 (Test) failed: {res2.summary}",
                    details=res2.details,
                    exit_code=res2.exit_code,
                    tier=2,
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                    command=res2.command,
                    cwd=str(base_dir),
                    skipped_tiers=skipped,
                    error_type="tier2_test",
                )
        else:
            skipped_tiers.append(2)
    else:
        skipped_tiers.append(2)

    # Tier 3
    if tier3_enabled:
        if check_git:
            effective_allow_empty = allow_empty_diff or (task_type.upper() == "READ_ONLY")
            res3 = verify_git_diff(cwd=base_dir, allow_empty=effective_allow_empty, max_deletions=max_deletions)
            if not res3.passed:
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    summary=f"Tier 3 (Git) failed: {res3.summary}",
                    details=res3.details,
                    exit_code=res3.exit_code,
                    tier=3,
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                    cwd=str(base_dir),
                    skipped_tiers=skipped_tiers,
                    error_type="tier3_git",
                )

        if claimed_deliverables:
            res_deliv = verify_deliverables(claimed_deliverables, cwd=base_dir)
            if not res_deliv.passed:
                return VerificationResult(
                    passed=False,
                    check_type="ladder",
                    summary=f"Tier 3 (Deliverables) failed: {res_deliv.summary}",
                    details=res_deliv.details,
                    exit_code=res_deliv.exit_code,
                    tier=3,
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                    cwd=str(base_dir),
                    skipped_tiers=skipped_tiers,
                    error_type="tier3_deliverables",
                )
    else:
        skipped_tiers.append(3)

    return VerificationResult(
        passed=True,
        check_type="ladder",
        summary="All active verification ladder tiers passed",
        exit_code=0,
        tier=None,
        duration_ms=(time.perf_counter() - t0) * 1000.0,
        cwd=str(base_dir),
        skipped_tiers=skipped_tiers,
    )
