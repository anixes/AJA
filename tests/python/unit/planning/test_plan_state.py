"""
test_plan_state.py - Unit tests for Living Disk State (PLAN.md) Manager.
========================================================================
"""

import os
import time
from pathlib import Path
import pytest

from aja.orchestration.plan_state import (
    AmbiguousTaskMatchError,
    PlanExistsError,
    PlanFileLock,
    PlanLockError,
    PlanParseError,
    PlanState,
    PlanTask,
    TaskNotFoundError,
    TaskStatus,
    init_plan,
    parse_plan,
    render_compact_progress,
    update_task_status,
)


def test_init_and_parse_roundtrip(tmp_path: Path):
    plan_file = tmp_path / "PLAN.md"
    tasks = [
        "Setup architecture",
        "Implement verification engine",
        "Write integration tests",
    ]

    state = init_plan("Build Autonomous Harness", tasks, path=plan_file)
    assert state.objective == "Build Autonomous Harness"
    assert state.total_count == 3
    assert state.completed_count == 0
    assert state.progress_pct == 0.0
    assert state.current_task.index == 1
    assert state.current_task.status == TaskStatus.TODO

    # Ensure PlanExistsError on overwrite=False
    with pytest.raises(PlanExistsError):
        init_plan("Another", ["Task"], path=plan_file, overwrite=False)

    # Read back from disk
    parsed = parse_plan(plan_file)
    assert parsed.objective == "Build Autonomous Harness"
    assert len(parsed.tasks) == 3
    assert [t.text for t in parsed.tasks] == tasks


def test_single_in_progress_invariant_and_demotion(tmp_path: Path):
    plan_file = tmp_path / "PLAN.md"
    tasks = ["Task 1", "Task 2", "Task 3"]
    init_plan("Test Invariant", tasks, path=plan_file)

    # 1. Set Task 1 to IN_PROGRESS
    s1 = update_task_status(plan_file, 1, TaskStatus.IN_PROGRESS)
    assert s1.tasks[0].status == TaskStatus.IN_PROGRESS
    assert s1.current_task.index == 1

    # 2. Set Task 2 to IN_PROGRESS -> Task 1 must be auto-demoted to TODO
    s2 = update_task_status(plan_file, 2, TaskStatus.IN_PROGRESS)
    assert s2.tasks[0].status == TaskStatus.TODO
    assert s2.tasks[1].status == TaskStatus.IN_PROGRESS
    assert s2.current_task.index == 2

    # 3. Mark Task 2 as DONE with evidence
    s3 = update_task_status(plan_file, 2, TaskStatus.DONE, evidence="tests passed 100%")
    assert s3.tasks[1].status == TaskStatus.DONE
    assert s3.tasks[1].evidence == "tests passed 100%"
    assert s3.completed_count == 1
    assert s3.progress_pct == 33.3

    # Ensure evidence persisted on disk
    reloaded = parse_plan(plan_file)
    assert reloaded.tasks[1].evidence == "tests passed 100%"


def test_text_matching_and_ambiguity_handling(tmp_path: Path):
    plan_file = tmp_path / "PLAN.md"
    tasks = [
        "Implement verification gate",
        "Implement living disk state",
        "Deploy application",
    ]
    init_plan("Text Matching", tasks, path=plan_file)

    # Unique match works
    s = update_task_status(plan_file, "living disk", TaskStatus.IN_PROGRESS)
    assert s.tasks[1].status == TaskStatus.IN_PROGRESS

    # Ambiguous match raises AmbiguousTaskMatchError
    with pytest.raises(AmbiguousTaskMatchError) as exc_info:
        update_task_status(plan_file, "Implement", TaskStatus.DONE)
    assert "#1" in str(exc_info.value)
    assert "#2" in str(exc_info.value)

    # Unknown task raises TaskNotFoundError
    with pytest.raises(TaskNotFoundError):
        update_task_status(plan_file, "Nonexistent Task", TaskStatus.DONE)


def test_fence_skipping_and_user_notes_preservation(tmp_path: Path):
    plan_file = tmp_path / "PLAN.md"
    custom_content = """# Objective
Refactor Agent

## Architecture & Notes
Here is an example code block with fake task checkboxes that should be ignored:
```markdown
- [ ] 99. Fake task inside code fence
```
And tilde fence:
~~~markdown
- [ ] 100. Fake task inside tilde fence
~~~

## Execution Plan
- [ ] 1. Real task one
  - User note under task one
- [ ] 2. Real task two

## Extra Section
User custom footer notes.
"""
    plan_file.write_text(custom_content, encoding="utf-8")

    parsed = parse_plan(plan_file)
    assert len(parsed.tasks) == 2
    assert parsed.tasks[0].text == "Real task one"
    assert parsed.tasks[1].text == "Real task two"

    # Update Task 1 status
    updated = update_task_status(plan_file, 1, TaskStatus.DONE)
    assert updated.tasks[0].status == TaskStatus.DONE

    # Verify that code fences and user notes are byte-preserved
    disk_text = plan_file.read_text(encoding="utf-8")
    assert "Fake task inside code fence" in disk_text
    assert "Fake task inside tilde fence" in disk_text
    assert "User note under task one" in disk_text
    assert "## Extra Section" in disk_text
    assert "User custom footer notes." in disk_text


def test_render_compact_progress(tmp_path: Path):
    plan_file = tmp_path / "PLAN.md"
    tasks = ["Alpha", "Beta", "Gamma"]
    init_plan("Deploy System", tasks, path=plan_file)
    update_task_status(plan_file, 1, TaskStatus.DONE)
    update_task_status(plan_file, 2, TaskStatus.IN_PROGRESS)

    progress = render_compact_progress(plan_file)
    assert "1/3 DONE (33.3%)" in progress
    assert "Deploy System" in progress
    assert 'Current Task: #2 "Beta" (IN_PROGRESS)' in progress
    assert 'Next Pending: #3 "Gamma"' in progress


def test_file_lock_and_stale_recovery(tmp_path: Path):
    target_file = tmp_path / "PLAN.md"
    lock_file = tmp_path / ".PLAN.md.lock"

    # Normal lock acquisition
    with PlanFileLock(target_file, timeout=1.0):
        assert lock_file.exists()

    # Lock is cleaned up
    assert not lock_file.exists()

    # Create synthetic stale lock (aged 10 seconds)
    import json
    lock_file.write_text(json.dumps({"pid": 999999, "time": time.time() - 10.0}), encoding="utf-8")

    # Acquisition should detect stale lock and reclaim it
    with PlanFileLock(target_file, timeout=2.0):
        assert lock_file.exists()

    assert not lock_file.exists()



def test_atomic_write_colocation_and_checksum_verification(tmp_path: Path):
    from aja.orchestration.plan_state import _atomic_write, PlanStateCorruptionError

    target_file = tmp_path / "subdir" / "PLAN.md"
    content = "# Objective\nTest colocation and checksum\n\n## Execution Plan\n- [ ] 1. Do thing\n"

    # 1. Normal atomic write
    _atomic_write(target_file, content)
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == content

    # Verify state_hash in parse_plan
    parsed = parse_plan(target_file)
    assert len(parsed.state_hash) == 16

    # 2. Test checksum mismatch detection
    from unittest.mock import patch
    with patch.object(Path, "read_bytes", return_value=b"corrupted bytes"):
        with pytest.raises(PlanStateCorruptionError) as exc_info:
            _atomic_write(target_file, content)
        assert "Post-write checksum verification failed" in str(exc_info.value)

