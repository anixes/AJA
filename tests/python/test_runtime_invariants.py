import os
import sys
import zlib
import json
import pytest
from pathlib import Path
from aja.runtime.execution.contracts import ExecutionRequest, ExecutionSession, StateTransitionError
from aja.runtime.execution.sequencer import EventSequencer, TelemetryEmitter


def test_fsm_transition_contracts(tmp_path):
    """Verify that ExecutionSession enforces strict FSM state transitions."""
    req = ExecutionRequest(command="echo FSM")
    session = ExecutionSession(
        session_id="test-fsm",
        request=req,
        state="created",
        trace_id="test-trace",
        run_id="test-run",
        root=tmp_path,
        manifest_path=tmp_path / "manifest.json",
    )
    
    # Valid shifting: created -> starting -> running -> completed
    session.transition_to("starting")
    assert session.state == "starting"
    
    session.transition_to("running")
    assert session.state == "running"
    
    session.transition_to("completed")
    assert session.state == "completed"

    # Reset state to starting and test invalid shift
    session.state = "starting"
    with pytest.raises(StateTransitionError):
        # Shift directly starting -> completed is prohibited
        session.transition_to("completed")


def test_framed_log_crc32_and_repair(tmp_path):
    """Verify framed append protocol writes checksums and recovers from corrupted log tails cleanly."""
    seq = EventSequencer(session_id="test-repair", trace_id="trace-repair")
    emitter = TelemetryEmitter(tmp_path, seq)

    # 1. Emit three valid framed log entries
    emitter.emit("EXECUTION_START", {"msg": "First"})
    emitter.emit("EXECUTION_STDOUT", {"line": "Second"})
    emitter.emit("EXECUTION_FINISHED", {"status": "Third"})

    # Validate file contains framed signatures
    lines = emitter.timeline_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all(line.startswith("FRAME:") for line in lines)

    # 2. Append a corrupted truncated frame at the tail
    with open(emitter.timeline_path, "a", encoding="utf-8") as f:
        f.write("FRAME:000000ff:badcrc32:{\"sequence\": 3, \"event_type\": \"EXECUTION_CORRUPT\", \"incomplete...\n")

    # 3. Invoke recovery check
    last_valid_seq = TelemetryEmitter.repair_journal(emitter.timeline_path)
    
    # Must recover and return sequence 2 (since index is 0, 1, 2)
    assert last_valid_seq == 2

    # Verify that the corrupted line was stripped and exactly 3 valid lines remain
    recovered_lines = emitter.timeline_path.read_text(encoding="utf-8").splitlines()
    assert len(recovered_lines) == 3
    assert not any("EXECUTION_CORRUPT" in l for l in recovered_lines)


def test_rust_token_batch_counting():
    """Verify PyO3 batch token counting correctly returns token lengths in a single GIL crossing."""
    try:
        import aja_native
        has_native = True
    except ImportError:
        has_native = False

    if not has_native:
        pytest.skip("aja_native module is not installed or available.")

    texts = [
        "AJA is a durable execution runtime.",
        "Deterministic replay depends on sequential event order.",
        "Chunky boundaries optimize PyO3 performance."
    ]

    # Verify batch results match individual tiktoken counts
    batch_res = aja_native.count_tokens_batch(texts)
    assert len(batch_res) == 3
    
    individual_res = [aja_native.count_tokens(t) for t in texts]
    assert batch_res == individual_res
