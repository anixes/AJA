"""Unit test for status command non-destructive baton inspection (Task 3.2 / M2).

Verifies that `aja status` does not delete expired batons or .arrow files,
and instead marks stale batons with `[stale]`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from rich.console import Console


def test_status_command_does_not_unlink_stale_batons(tmp_path: Path, capsys):
    from aja.cli.commands.status import cmd_status

    baton_dir = tmp_path / "batons"
    baton_dir.mkdir(parents=True, exist_ok=True)

    stale_json = baton_dir / "baton_stale1.json"
    stale_arrow = baton_dir / "baton_stale1.arrow"

    # Write a baton expired 2 hours ago with ttl=3600
    stale_time = time.time() - 7200
    stale_json.write_text(
        json.dumps(
            {
                "objective": "Build long pipeline",
                "timestamp": stale_time,
                "ttl": 3600,
            }
        ),
        encoding="utf-8",
    )
    stale_arrow.write_text("arrow_binary_data", encoding="utf-8")

    with patch("aja.cli.commands.status.DATA_DIR", tmp_path):
        cmd_status()

    # The baton files MUST still exist on disk!
    assert stale_json.exists(), "stale baton json was unexpectedly deleted!"
    assert stale_arrow.exists(), "stale baton arrow file was unexpectedly deleted!"

    captured = capsys.readouterr()
    # The output should show the stale marker
    assert "[stale]" in captured.out
    assert "Build long pipeline" in captured.out
