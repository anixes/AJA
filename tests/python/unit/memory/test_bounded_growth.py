"""
Bounded-growth regression tests:
- secretary.get_chat_history bounded window fetch (no full-table scan)
- secretary chat TTL prune in the write path
- temporal_graph default db path resolves under DATA_DIR
"""

import time
from pathlib import Path

import pytest

import aja.config
from aja.cognitive.temporal_graph import BiTemporalEntityGraph
from aja.memory.secretary import AJAMemory


@pytest.fixture()
def memory(tmp_path):
    return AJAMemory(db_path=str(tmp_path / "lancedb"))


def test_get_chat_history_returns_newest_n_ascending(memory, monkeypatch):
    """Seeding >limit rows must yield exactly `limit` newest rows, oldest-first."""
    limit = 5
    total = 25
    base = time.time()
    counter = {"n": 0}

    def fake_time():
        counter["n"] += 1
        return base + counter["n"]

    monkeypatch.setattr(time, "time", fake_time)
    for i in range(total):
        memory.mirror_chat_message("user" if i % 2 == 0 else "assistant", f"msg-{i}")

    history = memory.get_chat_history(limit=limit)

    assert len(history) == limit
    timestamps = [h["timestamp"] for h in history]
    assert timestamps == sorted(timestamps)
    # Newest window: messages total-limit .. total-1
    assert [h["content"] for h in history] == [f"msg-{i}" for i in range(total - limit, total)]


def test_chat_ttl_prune_deletes_stale_keeps_fresh(memory):
    """Backdated rows are deleted; fresh rows survive."""
    now = time.time()
    for i in range(4):
        memory.mirror_chat_message("user", f"fresh-{i}")
    for i in range(3):
        memory.mirror_chat_message("user", f"stale-{i}")

    # Backdate the stale half via direct table.update.
    table = memory.db.open_table("aja_chat_history")
    stale_ids = [
        r["message_id"] for r in table.search().to_list()
        if r["content"].startswith("stale-")
    ]
    assert len(stale_ids) == 3
    for mid in stale_ids:
        table.update(
            where=f"message_id = '{mid}'",
            values={"timestamp": float(now - 40 * 24 * 3600)},
        )

    removed = memory._prune_chat_history(ttl_days=30)
    assert removed == len(stale_ids)

    # Re-open: the old handle is pinned to the pre-delete table version.
    fresh_rows = memory.db.open_table("aja_chat_history").search().to_list()
    remaining = {r["message_id"] for r in fresh_rows}
    for mid in stale_ids:
        assert mid not in remaining

    contents = {r["content"] for r in fresh_rows}
    assert all(c.startswith("fresh-") for c in contents)


def test_temporal_graph_default_path_under_data_dir(tmp_path, monkeypatch):
    """Default db_path honors aja.config.DATA_DIR; never touches real home."""
    def _boom(*args, **kwargs):
        raise AssertionError("Path.home() must not be used when DATA_DIR is available")

    monkeypatch.setattr(Path, "home", _boom)
    monkeypatch.setattr(aja.config, "DATA_DIR", tmp_path / "ajadata")

    graph = BiTemporalEntityGraph()

    expected = tmp_path / "ajadata" / "temporal_graph.db"
    assert Path(graph.db_path) == expected
    assert expected.exists()


def test_temporal_graph_explicit_path_override(tmp_path):
    explicit = tmp_path / "custom" / "graph.db"
    graph = BiTemporalEntityGraph(db_path=explicit)
    assert Path(graph.db_path) == explicit
    assert explicit.exists()
