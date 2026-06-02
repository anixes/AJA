import pytest
import os
from pathlib import Path
from aja.config import PROJECT_ROOT, DATA_DIR
from aja.orchestration.activity_rt import Activity, ActivityType, RetryPolicy, ActivityRuntime
from aja.runtime.mission_journal import MissionJournal
import aja.runtime.execution.manager as _mgr_module


TEST_MISSION_ID = "test-mission-unit-ar"


# Pin anyio backend to asyncio for this test module.
# This prevents pytest-anyio from also running each test under Trio,
# which doubles the test count and causes trio-specific loop-handling differences.
@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param

@pytest.fixture(autouse=True)
def reset_execution_manager():
    """
    Force a fresh ExecutionManager for each test.

    The global _DEFAULT_MANAGER singleton retains asyncio handles tied to the
    event loop created by a previous asyncio.run() call. Python 3.10+ closes
    that loop after asyncio.run() returns, so the next test's asyncio.run()
    starts a *new* loop — but the stale manager still points at the old one,
    causing 'no current event loop' errors inside bus.publish() and similar
    helpers that call asyncio.get_event_loop().

    Resetting the singleton here ensures every test gets a clean manager
    bound to its own fresh event loop.
    """
    _mgr_module._DEFAULT_MANAGER = None
    yield
    _mgr_module._DEFAULT_MANAGER = None


@pytest.fixture()
def journal(tmp_path):
    """Fresh MissionJournal backed by a temp directory for each test."""
    journal_dir = tmp_path / "missions"
    journal_dir.mkdir(parents=True, exist_ok=True)

    # Monkey-patch DATA_DIR for journal so it writes to tmp_path
    import aja.runtime.mission_journal as jmod
    original_data_dir = jmod.DATA_DIR

    jmod.DATA_DIR = tmp_path
    j = MissionJournal(TEST_MISSION_ID)
    yield j

    jmod.DATA_DIR = original_data_dir


@pytest.mark.anyio
async def test_run_python_read_file(journal, tmp_path):
    """ActivityRuntime correctly runs a Python (read_file) tool and journals events."""
    temp_file = tmp_path / "test_read.txt"
    temp_file.write_text("Hello Activity Runtime!", encoding="utf-8")

    runtime = ActivityRuntime(journal=journal)
    activity = Activity(
        tool="read_file",
        args={"path": str(temp_file)},
        activity_type=ActivityType.PYTHON,
        trace_id="test-trace-python",
        retry_policy=RetryPolicy.SAFE,
    )

    result = await runtime.run(activity)

    assert result.success is True
    assert result.tool == "read_file"
    assert "Hello Activity Runtime!" in result.data

    events = journal.read_events()
    # Expected sequence: PERMISSION_GRANTED → TOOL_CALLED → TOOL_COMPLETED
    event_types = [e["event_type"] for e in events]
    assert "PERMISSION_GRANTED" in event_types, f"Missing PERMISSION_GRANTED in {event_types}"
    assert "TOOL_CALLED" in event_types, f"Missing TOOL_CALLED in {event_types}"
    assert "TOOL_COMPLETED" in event_types, f"Missing TOOL_COMPLETED in {event_types}"
    tool_called = next(e for e in events if e["event_type"] == "TOOL_CALLED")
    tool_completed = next(e for e in events if e["event_type"] == "TOOL_COMPLETED")
    assert tool_called["tool"] == "read_file"
    assert tool_completed["success"] is True


@pytest.mark.anyio
async def test_run_shell_dry_run(journal):
    """Dry-run shell activity returns a simulation result without executing anything."""
    runtime = ActivityRuntime(journal=journal, dry_run=True)
    activity = Activity(
        tool="run_shell_command",
        args={"cmd": "echo 'Hello Dry Run!'"},
        activity_type=ActivityType.SHELL,
        trace_id="test-trace-dry",
        retry_policy=RetryPolicy.NONE,
    )

    result = await runtime.run(activity)

    assert result.success is True
    assert "Successfully simulated" in result.stdout
    assert result.exit_code == 0
    assert result.env_state is not None
    assert result.env_state["exit_code"] == 0


@pytest.mark.anyio
async def test_run_shell_persistent_cwd(journal):
    """cd commands in dry-run mode update the runtime's persistent _shell_cwd."""
    runtime = ActivityRuntime(journal=journal, dry_run=True)

    # Initial CWD should be PROJECT_ROOT
    assert runtime._shell_cwd == str(PROJECT_ROOT)

    activity = Activity(
        tool="run_shell_command",
        args={"cmd": "cd .."},
        activity_type=ActivityType.SHELL,
        trace_id="test-trace-cd",
        retry_policy=RetryPolicy.NONE,
    )

    await runtime.run(activity)

    expected_parent = str(PROJECT_ROOT.parent.resolve())
    assert runtime._shell_cwd == expected_parent


@pytest.mark.anyio
async def test_command_guard_denial(journal):
    """CommandGuard blocks destructive commands before they reach the executor."""
    runtime = ActivityRuntime(journal=journal)
    activity = Activity(
        tool="run_shell_command",
        args={"cmd": "mkfs /dev/sda"},
        activity_type=ActivityType.SHELL,
        trace_id="test-trace-blocked",
        retry_policy=RetryPolicy.NONE,
    )

    result = await runtime.run(activity)

    assert result.success is False
    assert result.error is not None
    assert "CommandGuard blocked" in result.error

