"""
plan_state.py - Living Disk State (`PLAN.md`) Manager for AJA.
==============================================================
Provides production-grade autonomous agent working memory on disk:
- Atomic file writes with Windows PermissionError retry handling
- Cross-platform process locking (.lock with stale-PID recovery)
- Fence-aware, section-anchored markdown parsing (skips code blocks)
- Single in-progress task invariant (Devin / Manus pattern)
- Token-efficient compact progress rendering for LLM context injection
- 100% preservation of user notes, architecture sections, and custom markdown
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union


# ============================================================================
# Status Enum & Typed Exceptions
# ============================================================================

class TaskStatus(str, Enum):
    """Execution status for tasks in the Living Disk State."""
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"

    @classmethod
    def from_box(cls, char: str) -> TaskStatus:
        c = char.strip()
        if c == "/":
            return cls.IN_PROGRESS
        if c.lower() == "x":
            return cls.DONE
        if c == "-":
            return cls.CANCELLED
        return cls.TODO

    def to_box(self) -> str:
        if self == TaskStatus.IN_PROGRESS:
            return "/"
        if self == TaskStatus.DONE:
            return "x"
        if self == TaskStatus.CANCELLED:
            return "-"
        return " "


class PlanStateError(Exception):
    """Base exception for PlanState operations."""
    pass


class PlanStateCorruptionError(PlanStateError):
    """Raised when post-write checksum verification or disk integrity fails."""
    pass


class PlanLockError(PlanStateError):

    """Raised when plan file lock acquisition times out."""
    pass


class PlanParseError(PlanStateError):
    """Raised when PLAN.md has zero valid tasks or invalid syntax."""
    pass


class PlanExistsError(PlanStateError):
    """Raised when init_plan finds an existing file and overwrite=False."""
    pass


class AmbiguousTaskMatchError(PlanStateError):
    """Raised when task string matching resolves to more than one task."""
    pass


class TaskNotFoundError(PlanStateError):
    """Raised when task index or string match cannot be found."""
    pass


# ============================================================================
# Dataclasses
# ============================================================================

@dataclass
class PlanTask:
    """An individual actionable task within the Living Disk State."""
    index: int
    text: str
    status: TaskStatus
    phase: Optional[str] = None
    evidence: Optional[str] = None


@dataclass
class PlanState:
    """The in-memory projection of the disk-resident PLAN.md file."""
    objective: str
    tasks: List[PlanTask]
    plan_path: Path
    raw_content: str
    state_hash: str = ""
    revision: int = 0

    @property

    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status in (TaskStatus.DONE, TaskStatus.CANCELLED))

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def progress_pct(self) -> float:
        if self.total_count == 0:
            return 0.0
        return round((self.completed_count / self.total_count) * 100.0, 1)

    @property
    def current_task(self) -> Optional[PlanTask]:
        for t in self.tasks:
            if t.status == TaskStatus.IN_PROGRESS:
                return t
        for t in self.tasks:
            if t.status == TaskStatus.TODO:
                return t
        return None

    @property
    def is_complete(self) -> bool:
        return (
            self.total_count > 0
            and all(t.status in (TaskStatus.DONE, TaskStatus.CANCELLED) for t in self.tasks)
            and any(t.status == TaskStatus.DONE for t in self.tasks)
        )


# ============================================================================
# File Locking & Atomic Persistence
# ============================================================================

class PlanFileLock:
    """Process-level file locking with stale-lock expiration and retry backoff."""

    def __init__(self, target_path: Path, timeout: float = 5.0):
        self.target_path = Path(target_path)
        self.lock_path = self.target_path.with_name(f".{self.target_path.name}.lock")
        self.timeout = timeout
        self._acquired = False

    def __enter__(self):
        t0 = time.time()
        pid = os.getpid()

        while True:
            try:
                # O_CREAT | O_EXCL ensures atomic creation
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": pid, "time": time.time()}, f)
                self._acquired = True
                return self
            except FileExistsError:
                # Check for stale lock
                try:
                    if self.lock_path.exists():
                        content = self.lock_path.read_text(encoding="utf-8", errors="replace")
                        data = json.loads(content)
                        lock_age = time.time() - data.get("time", 0)
                        # Reclaim if stale (> 5 seconds)
                        if lock_age > self.timeout:
                            try:
                                self.lock_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                except Exception:
                    pass

                if time.time() - t0 > self.timeout:
                    raise PlanLockError(
                        f"Timed out after {self.timeout}s waiting for lock: {self.lock_path}"
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._acquired:
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._acquired = False


def _atomic_write(target_path: Path, content: str) -> None:
    """
    Write content to temporary file colocated in target's directory and replace target atomically.
    Includes SHA-256 pre/post write verification and 3-attempt exponential backoff
    for Windows PermissionError. Files are normalized to LF on write.
    """
    import hashlib

    target = Path(target_path).resolve()
    parent_dir = target.parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    # Unique temporary file strictly in the target's parent directory (same filesystem/volume)
    tmp_path = parent_dir / f".{target.name}.{os.getpid()}_{time.time_ns()}.tmp"
    normalized = content.replace("\r\n", "\n")
    encoded = normalized.encode("utf-8")
    expected_hash = hashlib.sha256(encoded).hexdigest()

    tmp_path.write_bytes(encoded)

    last_error: Optional[Exception] = None
    backoff = 0.05

    for attempt in range(3):
        try:
            os.replace(str(tmp_path), str(target))
            # Post-write SHA-256 verification
            written_bytes = target.read_bytes()
            actual_hash = hashlib.sha256(written_bytes).hexdigest()
            if actual_hash != expected_hash:
                raise PlanStateCorruptionError(
                    f"Post-write checksum verification failed for '{target}'! "
                    f"Expected {expected_hash}, got {actual_hash}"
                )
            return
        except PermissionError as e:
            last_error = e
            time.sleep(backoff)
            backoff *= 2
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            raise e

    tmp_path.unlink(missing_ok=True)
    if last_error:
        raise last_error



# ============================================================================
# Core API: Init, Parse, Update, Render
# ============================================================================

def init_plan(
    objective: str,
    tasks: Sequence[str],
    path: Union[Path, str] = "PLAN.md",
    overwrite: bool = False,
) -> PlanState:
    """Initialize a new Living Disk State document."""
    plan_path = Path(path).resolve()

    if plan_path.exists() and not overwrite:
        raise PlanExistsError(
            f"Plan file already exists: {plan_path}. Set overwrite=True to replace."
        )

    task_lines = []
    for idx, t in enumerate(tasks, start=1):
        clean_text = t.strip()
        task_lines.append(f"- [ ] {idx}. {clean_text}")

    content = (
        f"# Objective\n{objective.strip()}\n\n"
        f"## Execution Plan\n" + "\n".join(task_lines) + "\n\n"
        f"## Verification & Notes\n"
    )

    with PlanFileLock(plan_path):
        _atomic_write(plan_path, content)

    return parse_plan(plan_path)


def parse_plan(path: Union[Path, str] = "PLAN.md") -> PlanState:
    """
    Parse Living Disk State markdown.
    Skips code fences (``` and ~~~) and normalizes CRLF to LF.
    """
    plan_path = Path(path).resolve()
    if not plan_path.is_file():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")

    raw = plan_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    lines = raw.split("\n")

    objective = ""
    tasks: List[PlanTask] = []
    in_code_fence = False
    fence_char = ""
    in_objective = False
    in_plan = False

    task_regex = re.compile(r"^\s*-\s*\[([ xX/\-])\]\s*(\d+)?[\.\)]?\s*(.*)$")
    evidence_regex = re.compile(r"^\s+-\s*evidence:\s*(.*)$", re.IGNORECASE)

    current_task: Optional[PlanTask] = None
    task_counter = 1

    for line in lines:
        stripped = line.strip()

        # Handle code fences
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_code_fence:
                in_code_fence = True
                fence_char = marker
                continue
            elif marker == fence_char:
                in_code_fence = False
                fence_char = ""
                continue

        if in_code_fence:
            continue

        # Section headers
        if stripped.startswith("# Objective"):
            in_objective = True
            in_plan = False
            continue
        elif stripped.startswith("## Execution Plan"):
            in_objective = False
            in_plan = True
            continue
        elif stripped.startswith("#"):
            in_objective = False
            in_plan = False

        if in_objective and stripped:
            objective = stripped if not objective else f"{objective}\n{stripped}"
            continue

        if in_plan:
            # Check for evidence sub-line
            ev_match = evidence_regex.match(line)
            if ev_match and current_task:
                current_task.evidence = ev_match.group(1).strip()
                continue

            # Check for task checkbox
            task_match = task_regex.match(line)
            if task_match:
                box_char, num_str, task_text = task_match.groups()
                status = TaskStatus.from_box(box_char)
                idx = int(num_str) if (num_str and num_str.isdigit()) else task_counter
                task_counter = max(task_counter + 1, idx + 1)

                task_obj = PlanTask(
                    index=idx,
                    text=task_text.strip(),
                    status=status,
                )
                tasks.append(task_obj)
                current_task = task_obj

    if not tasks:
        raise PlanParseError(
            f"No valid tasks found in plan file: {plan_path}. "
            "Ensure tasks match '- [ ] 1. <description>' under '## Execution Plan'."
        )

    import hashlib
    state_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    return PlanState(
        objective=objective or "Unspecified Objective",
        tasks=tasks,
        plan_path=plan_path,
        raw_content=raw,
        state_hash=state_hash,
    )



def _resolve_task_index(tasks: List[PlanTask], identifier: Union[int, str]) -> int:
    """Resolve an integer or string identifier to an exact task index."""
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        target_idx = int(identifier)
        for t in tasks:
            if t.index == target_idx:
                return target_idx
        raise TaskNotFoundError(f"Task with index {target_idx} not found in plan.")

    query = str(identifier).strip().lower()
    matches: List[PlanTask] = []

    for t in tasks:
        if query == t.text.strip().lower():
            # Exact match takes immediate precedence
            return t.index
        if query in t.text.strip().lower():
            matches.append(t)

    if len(matches) == 1:
        return matches[0].index
    if len(matches) > 1:
        candidates = [f"#{m.index} '{m.text}'" for m in matches]
        raise AmbiguousTaskMatchError(
            f"Task match '{identifier}' is ambiguous. Matching candidates: {', '.join(candidates)}"
        )

    raise TaskNotFoundError(f"No task matching '{identifier}' found in plan.")


def update_task_status(
    path: Union[Path, str],
    task_index_or_match: Union[int, str],
    status: Union[TaskStatus, str],
    evidence: Optional[str] = None,
) -> PlanState:
    """
    Atomically update task status on disk.
    - Preserves all non-task lines and user notes.
    - Enforces single in-progress invariant (auto-demoting prior in-progress tasks to todo).
    - Writes canonical evidence sub-line under the task.
    """
    plan_path = Path(path).resolve()

    # Convert status to TaskStatus
    if isinstance(status, str):
        st_clean = status.strip().lower()
        try:
            target_status = TaskStatus(st_clean)
        except ValueError:
            box_stat = TaskStatus.from_box(st_clean)
            target_status = box_stat
    else:
        target_status = status

    with PlanFileLock(plan_path):
        current_state = parse_plan(plan_path)
        target_idx = _resolve_task_index(current_state.tasks, task_index_or_match)

        # Single in-progress invariant: if target is IN_PROGRESS, find any other currently in-progress task
        demote_idx: Optional[int] = None
        if target_status == TaskStatus.IN_PROGRESS:
            for t in current_state.tasks:
                if t.status == TaskStatus.IN_PROGRESS and t.index != target_idx:
                    demote_idx = t.index
                    break

        lines = current_state.raw_content.split("\n")
        new_lines: List[str] = []
        in_code_fence = False
        fence_char = ""
        in_plan = False

        task_regex = re.compile(r"^(\s*-\s*\[)([ xX/\-])(\]\s*)(\d+)?([\.\)]?\s*)(.*)$")
        evidence_regex = re.compile(r"^\s+-\s*evidence:\s*(.*)$", re.IGNORECASE)

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("```") or stripped.startswith("~~~"):
                marker = stripped[:3]
                if not in_code_fence:
                    in_code_fence = True
                    fence_char = marker
                elif marker == fence_char:
                    in_code_fence = False
                    fence_char = ""
                new_lines.append(line)
                i += 1
                continue

            if in_code_fence:
                new_lines.append(line)
                i += 1
                continue

            if stripped.startswith("## Execution Plan"):
                in_plan = True
                new_lines.append(line)
                i += 1
                continue
            elif stripped.startswith("#"):
                in_plan = False

            if in_plan:
                m = task_regex.match(line)
                if m:
                    pre_box, old_box, post_box, num_str, sep, text = m.groups()
                    current_num = int(num_str) if (num_str and num_str.isdigit()) else None

                    # 1. Target task update
                    if current_num == target_idx:
                        new_box = target_status.to_box()
                        updated_line = f"{pre_box}{new_box}{post_box}{num_str or target_idx}{sep}{text}"
                        new_lines.append(updated_line)

                        # Check if next line is already an evidence sub-line
                        has_existing_evidence = False
                        if i + 1 < len(lines) and evidence_regex.match(lines[i + 1]):
                            has_existing_evidence = True

                        if evidence:
                            clean_ev = evidence.strip().replace("\n", " ")
                            ev_line = f"  - evidence: {clean_ev}"
                            new_lines.append(ev_line)
                            if has_existing_evidence:
                                i += 1  # Skip the old evidence line
                        elif has_existing_evidence:
                            # Keep existing evidence intact
                            new_lines.append(lines[i + 1])
                            i += 1

                        i += 1
                        continue

                    # 2. Auto-demote previous in-progress task if invariant triggered
                    elif demote_idx is not None and current_num == demote_idx:
                        demoted_line = f"{pre_box} {post_box}{num_str or demote_idx}{sep}{text}"
                        new_lines.append(demoted_line)
                        i += 1
                        continue

            new_lines.append(line)
            i += 1

        updated_content = "\n".join(new_lines)
        _atomic_write(plan_path, updated_content)

    return parse_plan(plan_path)


def render_compact_progress(
    path: Union[Path, str] = "PLAN.md",
    max_chars: int = 1200,
) -> str:
    """
    Format token-efficient progress block for dynamic prompt injection.
    Prioritizes active task and next pending task, omitting completed task details.
    """
    plan = parse_plan(path)
    lines: List[str] = [
        f"[PLAN: {plan.completed_count}/{plan.total_count} DONE ({plan.progress_pct}%) | Objective: \"{plan.objective}\"]"
    ]

    curr = plan.current_task
    if curr:
        lines.append(f"Current Task: #{curr.index} \"{curr.text}\" ({curr.status.value.upper()})")
        # Find next todo task after current
        next_task = next((t for t in plan.tasks if t.index > curr.index and t.status == TaskStatus.TODO), None)
        if next_task:
            lines.append(f"Next Pending: #{next_task.index} \"{next_task.text}\"")
    elif plan.is_complete:
        lines.append("Status: All planned tasks completed.")
    else:
        lines.append("Status: Ready to begin next task.")

    rendered = "\n".join(lines)
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars - 15].rstrip() + "\n...[truncated]"
    return rendered
