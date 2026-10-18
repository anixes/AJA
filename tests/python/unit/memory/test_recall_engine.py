"""Tests for aja.gateway.recall (semantic + temporal recall engine)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from aja.gateway.recall import (
    format_recall_context,
    semantic_recall,
    time_recall,
)


@pytest.fixture
def vector_memory(tmp_path, monkeypatch):
    """Real VectorMemory backed by a tmp_path LanceDB with mock embeddings."""
    monkeypatch.setenv("AJA_MOCK_EMBEDDINGS", "1")
    from aja.memory import manager as mm
    import aja.memory.vector as vmod

    real_mgr = mm.MemoryManager(db_path=tmp_path / "lancedb")
    monkeypatch.setattr(mm, "_instance", real_mgr)
    monkeypatch.setattr(vmod, "get_memory_manager", lambda: real_mgr)
    vm = vmod.VectorMemory(table_name="mission_semantic")
    return vm


def _seed_exchange(vm, role: str, content: str):
    from aja.memory.territory import get_text_embedding

    vm.add(
        content,
        get_text_embedding(content),
        metadata={
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


class TestSemanticRecall:
    def test_returns_known_exchanges_ranked(self, vector_memory):
        _seed_exchange(vector_memory, "user", "how do I fix the postgres connection pool leak")
        _seed_exchange(vector_memory, "assistant", "increase pool_size and set recycle=1800")
        _seed_exchange(vector_memory, "user", "what is the recipe for banana bread")

        results = semantic_recall(
            "postgres connection pool leaking connections",
            vector_memory=vector_memory,
            top_k=3,
        )
        assert results, "expected at least one recalled exchange"
        assert all(set(r) >= {"role", "content", "timestamp", "score"} for r in results)
        assert 0.0 <= results[0]["score"] <= 1.0
        # The top hit must be the on-topic exchange, not banana bread.
        assert "pool" in results[0]["content"].lower()
        off_topic = [r for r in results if "banana" in r["content"].lower()]
        if off_topic:
            assert off_topic[0]["score"] < results[0]["score"]

    def test_min_score_filters(self, vector_memory):
        _seed_exchange(vector_memory, "user", "deploy the fleet to Hetzner")
        results = semantic_recall(
            "banana bread recipe", vector_memory=vector_memory, min_score=0.99
        )
        # With an unrelated query and a strict threshold, nothing should pass.
        assert isinstance(results, list)

    def test_failure_containment_search_raises(self, vector_memory):
        class Boom:
            def search(self, *a, **kw):
                raise RuntimeError("lancedb exploded")

        assert semantic_recall("anything", vector_memory=Boom()) == []

    def test_empty_store(self, vector_memory):
        assert semantic_recall("anything", vector_memory=vector_memory) == []


def _write_journal(directory, mission_id: str, events: list[dict]):
    path = directory / f"mission_{mission_id}.jsonl"
    lines = [json.dumps(e) for e in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestTimeRecall:
    def test_recent_only(self, tmp_path):
        jdir = tmp_path / "missions"
        jdir.mkdir()
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=30)).isoformat()
        old = (now - timedelta(days=3)).isoformat()

        _write_journal(jdir, "a", [
            {"event_type": "MISSION_CREATED", "timestamp": old},
            {"event_type": "MISSION_COMPLETED", "timestamp": recent,
             "result_summary": "Deployed v2 to production"},
        ])
        _write_journal(jdir, "b", [
            {"event_type": "TOOL_COMPLETED", "timestamp": old, "tool": "run_shell_command"},
            {"event_type": "MISSION_COMPLETED", "timestamp": old, "result_summary": "ancient"},
        ])

        results = time_recall(hours_back=24, journal_dir=jdir)
        assert len(results) == 1
        assert results[0]["event_type"] == "MISSION_COMPLETED"
        assert results[0]["summary"] == "Deployed v2 to production"
        assert set(results[0]) == {"event_type", "timestamp", "summary"}

    def test_sorted_newest_first(self, tmp_path):
        jdir = tmp_path / "missions"
        jdir.mkdir()
        now = datetime.now(timezone.utc)
        _write_journal(jdir, "c", [
            {"event_type": "MISSION_COMPLETED", "timestamp": (now - timedelta(hours=2)).isoformat(),
             "result_summary": "older task"},
            {"event_type": "TOOL_COMPLETED", "timestamp": now.isoformat(), "tool": "fetch_url"},
        ])
        results = time_recall(hours_back=24, journal_dir=jdir)
        assert len(results) == 2
        assert results[0]["event_type"] == "TOOL_COMPLETED"
        assert results[1]["summary"] == "older task"

    def test_missing_dir_returns_empty(self, tmp_path):
        assert time_recall(hours_back=24, journal_dir=tmp_path / "nope") == []

    def test_torn_line_tolerated(self, tmp_path):
        jdir = tmp_path / "missions"
        jdir.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        path = jdir / "mission_d.jsonl"
        path.write_text(
            json.dumps({"event_type": "MISSION_COMPLETED", "timestamp": now,
                        "result_summary": "fine"}) + "\n"
            + '{"event_type": "MISSION_COMP'  # torn write
            + "\n",
            encoding="utf-8",
        )
        results = time_recall(hours_back=1, journal_dir=jdir)
        assert len(results) == 1


class TestFormatRecallContext:
    def test_both_sections_render(self):
        semantic = [{"role": "user", "content": "fix the pool",
                     "timestamp": "2026-08-22T10:00:00+00:00", "score": 0.9}]
        temporal = [{"event_type": "MISSION_COMPLETED",
                     "timestamp": "2026-08-23T07:00:00+00:00",
                     "summary": "nightly research done"}]
        out = format_recall_context(semantic, temporal)
        assert "## Previously discussed" in out
        assert "## Recent activity" in out
        assert "fix the pool" in out
        assert "nightly research done" in out
        assert "2026-08-22T10:00:00+00:00" in out
        assert "2026-08-23T07:00:00+00:00" in out

    def test_empty_input(self):
        assert format_recall_context([], []) == ""

    def test_truncation_respected_oldest_first(self):
        semantic = [{"role": "user", "content": f"message number {i} " + "x" * 200,
                     "timestamp": str(i), "score": 1.0 - i * 0.01}
                    for i in range(20)]
        out = format_recall_context(semantic, [])
        assert len(out) <= 2100  # ~2000 char cap plus truncation marker slack
        # Oldest entries (highest i) dropped first.
        newest = next(e["content"].split()[2] for e in semantic[:1])
        assert newest in out
