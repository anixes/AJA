"""Regression tests for crash-corruption self-healing in AJAMemory.

A hard power-cut during a table write can leave a LanceDB table with an
empty (zero-field) schema; every query then fails with
``LanceError(Schema): No field named ...`` and the autonomous worker
error-loops. These tests pin the empty-schema repair pass:

- empty-schema known tables are dropped + recreated with canonical schemas
- aja_missions projections are rebuilt from the JSONL journals (source of truth)
- non-empty-schema tables and unknown tables are never touched
- a per-table repair failure never blocks startup

API note (lancedb 0.30.2, verified live): ``db.create_table(name)`` with no
args raises ValueError; an empty-schema table is produced by
``db.create_table(name, schema=pa.schema([]))`` — that is the corruption
signature reproduced here.
"""

import json
import logging

import lancedb
import pyarrow as pa
import pytest

from aja.memory import secretary as sec_module
from aja.memory.secretary import AJAMemory, MISSIONS_SCHEMA, list_tables_defensive


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Hermetic DATA_DIR redirect for both module globals the repair path
    resolves through (secretary constants + mission journal replay target)."""
    monkeypatch.setattr(sec_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr("aja.runtime.mission_journal.DATA_DIR", tmp_path)
    monkeypatch.setattr(sec_module, "_instance", None)
    return tmp_path


def _connect(env):
    return lancedb.connect(str(env / "lancedb"))


def _corrupt(db, name):
    """Reproduces the crash signature observed on this machine: a listed
    table whose schema has zero fields."""
    db.create_table(name, schema=pa.schema([]))


def _field_names(db, name):
    return list(db.open_table(name).schema.names)


class TestMissionsRepair:
    def test_empty_schema_missions_table_is_repaired(self, env, caplog):
        db = _connect(env)
        _corrupt(db, "aja_missions")
        assert _field_names(db, "aja_missions") == []

        with caplog.at_level(logging.WARNING, logger="aja.memory.secretary"):
            mem = AJAMemory(db_path=str(env / "lancedb"))

        repaired = db.open_table("aja_missions")
        assert repaired.schema.equals(MISSIONS_SCHEMA)
        # The incident signature: filtered queries on the repaired table work.
        assert mem.list_missions(status="PENDING") == []
        assert any(
            "repaired crash-corrupted empty-schema table 'aja_missions'" in r.message
            for r in caplog.records
        )

    def test_repair_rebuilds_projections_from_journal(self, env):
        journal_dir = env / "missions"
        journal_dir.mkdir(parents=True)
        events = [
            {
                "event_type": "MISSION_CREATED",
                "event_schema_version": "1.0",
                "mission_id": "wave3test",
                "sequence": 0,
                "timestamp": "2026-08-25T00:00:00+00:00",
                "goal": "restore history after power cut",
                "priority": 2,
                "metadata": {},
            },
            {
                "event_type": "MISSION_STATUS_CHANGED",
                "event_schema_version": "1.0",
                "mission_id": "wave3test",
                "sequence": 1,
                "timestamp": "2026-08-25T00:01:00+00:00",
                "from": "PENDING",
                "to": "ACTIVE",
            },
        ]
        (journal_dir / "mission_wave3test.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )

        db = _connect(env)
        _corrupt(db, "aja_missions")

        mem = AJAMemory(db_path=str(env / "lancedb"))

        restored = mem.get_mission("wave3test")
        assert restored is not None
        assert restored["goal"] == "restore history after power cut"
        assert restored["status"] == "ACTIVE"
        assert restored["priority"] == 2


class TestRepairBoundaries:
    def test_nonempty_and_unknown_tables_never_touched(self, env):
        db = _connect(env)
        # Healthy-known table carrying a NON-canonical but non-empty schema:
        # must survive init untouched (repair only keys on zero-field count).
        db.create_table(
            "aja_tasks", schema=pa.schema([("legacy_col", pa.string())])
        )
        # Unknown table even with the corruption signature: out of scope.
        _corrupt(db, "aja_unknown")

        AJAMemory(db_path=str(env / "lancedb"))

        assert _field_names(db, "aja_tasks") == ["legacy_col"]
        assert "aja_unknown" in set(list_tables_defensive(db))
        assert _field_names(db, "aja_unknown") == []  # still corrupt, untouched

    def test_multiple_known_tables_all_repaired(self, env):
        db = _connect(env)
        for name in ("aja_workers", "aja_missions", "aja_chat_history"):
            _corrupt(db, name)

        AJAMemory(db_path=str(env / "lancedb"))

        from aja.memory.secretary import (
            CHAT_HISTORY_SCHEMA,
            WORKERS_SCHEMA,
        )

        assert db.open_table("aja_workers").schema.equals(WORKERS_SCHEMA)
        assert db.open_table("aja_missions").schema.equals(MISSIONS_SCHEMA)
        assert db.open_table("aja_chat_history").schema.equals(CHAT_HISTORY_SCHEMA)


class TestRepairIsolation:
    def test_single_table_failure_does_not_block_startup(self, env, monkeypatch):
        db = _connect(env)
        _corrupt(db, "aja_missions")
        _corrupt(db, "aja_chat_history")

        real_connect = lancedb.connect

        class _FailingDropDB:
            """Proxy that makes drop_table fail for exactly one table."""

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, item):
                return getattr(self._inner, item)

            def drop_table(self, name, *args, **kwargs):
                if name == "aja_chat_history":
                    raise RuntimeError("simulated IO failure during drop")
                return self._inner.drop_table(name, *args, **kwargs)

        monkeypatch.setattr(
            sec_module.lancedb,
            "connect",
            lambda path: _FailingDropDB(real_connect(path)),
        )

        # Must not raise despite chat_history repair blowing up.
        mem = AJAMemory(db_path=str(env / "lancedb"))

        # Missions was still repaired; broken chat_history left as-is but
        # startup survived.
        assert db.open_table("aja_missions").schema.equals(MISSIONS_SCHEMA)
        assert _field_names(real_connect(str(env / "lancedb")), "aja_chat_history") == []
