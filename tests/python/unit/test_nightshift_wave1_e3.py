"""Night-shift Wave 1 E3 regression tests.

Covers T3 findings F1-F5 (peer brief T3.md) and A4 finding F1 (peer brief A4.md):
- failure_memory reader tolerance for records missing plan_node_ids / with
  mismatched-dim goal_embedding; react_executor in-loop writer includes
  plan_node_ids
- react_executor._record_repair aligned with PlanStore.record_repair signature
- skill_executor accepts both normalized ("id"/list tool_sequence) and raw
  SkillStore row shapes; recommend_skill exists and normalizes rows
- vector.search tolerates null/malformed metadata cells
- skill_compiler None-domain / None-payload trajectories
- autonomous_loop main_loop releases lock + stops heartbeat/intent engine on
  ANY exit path (stop event, external cancel, early failure)
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# T3#1 — FailureMemory reader/writer contract
# ---------------------------------------------------------------------------


class TestFailureMemoryContract:
    @pytest.fixture(autouse=True)
    def _isolated_state(self):
        from aja.planning import failure_memory as fm

        self.fm = fm
        self._orig_failures = fm.FailureMemory._failures
        self._orig_loaded = fm.FailureMemory._loaded
        fm.FailureMemory._failures = []
        fm.FailureMemory._loaded = True  # never touch disk
        yield
        fm.FailureMemory._failures = self._orig_failures
        fm.FailureMemory._loaded = self._orig_loaded

    def _plan(self):
        return SimpleNamespace(
            primitive_nodes=lambda: [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
        )

    def test_reader_tolerates_missing_plan_node_ids(self):
        """Legacy/in-loop records without plan_node_ids must not KeyError."""
        self.fm.FailureMemory._failures.append(
            {
                "goal": "deploy the thing",
                "goal_embedding": [1.0, 0.0, 0.0],
                "node": "a",
            }
        )
        emb = MagicMock()
        emb.embed_text.return_value = [1.0, 0.0, 0.0]
        with patch.object(self.fm, "EmbeddingService", return_value=emb):
            penalty = self.fm.FailureMemory.get_failure_penalty(
                "deploy the thing", self._plan()
            )
        assert penalty == 0.0  # empty f_nodes_set → node_sim 0.0

    def test_reader_tolerates_none_goal_embedding(self):
        self.fm.FailureMemory._failures.append({"goal": "x", "goal_embedding": None})
        emb = MagicMock()
        emb.embed_text.return_value = [1.0, 0.0]
        with patch.object(self.fm, "EmbeddingService", return_value=emb):
            penalty = self.fm.FailureMemory.get_failure_penalty("x", self._plan())
        assert penalty == 0.0

    def test_reader_tolerates_dimension_mismatch(self):
        """Embedding-backend switch → dim mismatch ValueError must be skipped."""
        self.fm.FailureMemory._failures.append(
            {
                "goal": "same goal",
                "goal_embedding": [1.0] * 1536,
                "plan_node_ids": ["a"],
            }
        )
        emb = MagicMock()
        emb.embed_text.return_value = [1.0] * 384
        with patch.object(self.fm, "EmbeddingService", return_value=emb):
            penalty = self.fm.FailureMemory.get_failure_penalty(
                "same goal", self._plan()
            )
        assert penalty == 0.0

    def test_matching_record_yields_positive_penalty(self):
        emb = MagicMock()
        emb.embed_text.return_value = [1.0, 0.0]
        self.fm.FailureMemory._failures.append(
            {
                "goal": "ship it",
                "goal_embedding": [1.0, 0.0],
                "plan_node_ids": ["a", "b"],
            }
        )
        with patch.object(self.fm, "EmbeddingService", return_value=emb):
            penalty = self.fm.FailureMemory.get_failure_penalty("ship it", self._plan())
        assert penalty > 0.9

    def test_inloop_writer_includes_plan_node_ids(self):
        """Both react_executor FailureMemory writers must persist plan_node_ids."""
        import inspect

        from aja.planning import react_executor as rex

        src = inspect.getsource(rex)
        # one at the in-loop failure site + one at the escalation site
        assert src.count('"plan_node_ids":') >= 2
        assert "[n.id for n in self.graph.primitive_nodes()]" in src


# ---------------------------------------------------------------------------
# T3#4 — PlanStore.record_repair call-site signature
# ---------------------------------------------------------------------------


class TestRecordRepairSignature:
    def test_record_repair_uses_real_signature(self):
        from aja.planning.replanner import RepairRecord
        from aja.planning.react_executor import ReActExecutor

        executor = object.__new__(ReActExecutor)
        executor.plan_id = "plan-123"
        rec = RepairRecord(
            node_id="node-a",
            attempt=2,
            failure_kind="logic",
            action_taken="escalate",
            timestamp="2026-08-25T00:00:00Z",
            notes="note",
        )
        executor.repair_history = [rec]

        mock_store_cls = MagicMock()
        with patch("aja.planning.react_executor.PlanStore", mock_store_cls):
            executor._record_repair(
                SimpleNamespace(id="node-a"), "escalate"
            )

        mock_store_cls.record_repair.assert_called_once_with(
            plan_id="plan-123",
            node_id="node-a",
            action="escalate",
            metadata={"attempt": 2, "failure_kind": "logic", "notes": "note"},
        )

    def test_planstore_record_repair_accepts_call(self):
        """The real PlanStore.record_repair signature matches what we send."""
        import inspect

        from aja.planning.plan_store import PlanStore

        params = inspect.signature(PlanStore.record_repair).parameters
        assert list(params) == ["plan_id", "node_id", "action", "metadata"]


# ---------------------------------------------------------------------------
# T3#2 — skill_executor read-site normalization + recommend_skill existence
# ---------------------------------------------------------------------------


class TestSkillExecutorNormalization:
    def _run_execute_skill(self, skill, execute_step_mock):
        import aja.skills.skill_executor as se

        with patch.object(se, "_refresh_last_used"), patch.object(
            se, "_risk_gate", return_value=True
        ), patch.object(se, "check_environment", return_value=(True, [])), \
             patch.object(se, "_update_skill_metrics"), patch.object(
            se, "_load_completed_steps", return_value={}
        ), patch.object(se, "_checkpoint_step"), patch.object(
            se, "_clear_checkpoints"
        ), patch.object(se, "_execute_step", execute_step_mock):
            return se.execute_skill(
                skill,
                task_id=1,
                run_id="run-abc",
                objective="do it",
            )

    def test_raw_store_row_with_skill_id_and_list_sequence(self):
        """Raw SkillStore rows (skill_id + decoded tool_sequence list) work."""
        step_mock = MagicMock(return_value=(True, "ok", None))
        raw_row = {
            "skill_id": "raw-row-id",
            "name": "Raw",
            "tool_sequence": [{"tool_name": "shell", "args": {"cmd": "echo hi"}}],
        }
        assert self._run_execute_skill(raw_row, step_mock) is True
        assert step_mock.call_count == 1

    def test_normalized_row_with_id_key(self):
        step_mock = MagicMock(return_value=(True, "ok", None))
        normalized = {
            "id": "norm-id",
            "name": "Norm",
            "tool_sequence": json.dumps(
                [{"tool_name": "shell", "args": {}}]
            ),
        }
        assert self._run_execute_skill(normalized, step_mock) is True
        assert step_mock.call_count == 1

    def test_string_tool_sequence_still_parsed(self):
        step_mock = MagicMock(return_value=(True, "ok", None))
        skill = {
            "id": "legacy",
            "tool_sequence": json.dumps([{"tool_name": "t"}]),
        }
        assert self._run_execute_skill(skill, step_mock) is True

    def test_missing_sequence_falls_back_cleanly(self):
        step_mock = MagicMock()
        skill = {"id": "empty"}
        result = self._run_execute_skill(skill, step_mock)
        assert result is False
        step_mock.assert_not_called()


class TestRecommendSkill:
    def test_recommend_skill_exists_and_importable(self):
        from aja.skills.skill_composer import build_chain  # noqa: F401
        from aja.skills.skill_store import recommend_skill  # noqa: F401

    def test_normalize_skill_row_maps_both_shapes(self):
        from aja.skills.skill_store import normalize_skill_row

        row = {
            "skill_id": "sid-1",
            "name": "Deploy",
            "risk_level": "HIGH",
            "confidence_score": 0.9,
            "tool_sequence_json": json.dumps([{"tool_name": "shell"}]),
            "tags_json": json.dumps(["ops"]),
        }
        norm = normalize_skill_row(row)
        assert norm["id"] == "sid-1"
        assert norm["tool_sequence"] == [{"tool_name": "shell"}]
        assert norm["tags"] == ["ops"]
        # original keys preserved
        assert norm["skill_id"] == "sid-1"

    def test_normalize_tolerates_corrupt_json(self):
        from aja.skills.skill_store import normalize_skill_row

        norm = normalize_skill_row(
            {"skill_id": "s", "tool_sequence_json": "{not-json", "tags_json": None}
        )
        assert norm["tool_sequence"] == []
        assert norm["tags"] == []
        assert norm["id"] == "s"

    def test_recommend_skill_returns_normalized_or_none(self):
        from aja.skills.skill_store import recommend_skill

        store = MagicMock()
        store.search_skills.return_value = [
            {
                "skill_id": "best",
                "confidence_score": 0.8,
                "updated_at": "2026-08-01T00:00:00Z",
                "tool_sequence_json": "[1]",
                "tags_json": "[]",
            }
        ]
        with patch("aja.skills.skill_store.SkillStore", return_value=store):
            got = recommend_skill("deploy app")
        assert got is not None
        assert got["id"] == "best"
        assert got["tool_sequence"] == [1]

        with patch("aja.skills.skill_store.SkillStore", return_value=store):
            assert recommend_skill("deploy app", min_confidence=0.99) is None


# ---------------------------------------------------------------------------
# T3#3 — VectorMemory.search null/malformed metadata guard
# ---------------------------------------------------------------------------


def _fake_vector_memory(rows):
    """Builds a VectorMemory whose search chain returns *rows* (no LanceDB)."""
    from aja.memory.vector import VectorMemory

    vm = object.__new__(VectorMemory)
    vm.table_name = "agent_memory"

    arrow_result = SimpleNamespace(to_pylist=lambda: rows)
    query = SimpleNamespace(
        where=lambda *_: query,
        limit=lambda *_: SimpleNamespace(to_arrow=lambda: arrow_result),
    )
    table = SimpleNamespace(search=lambda _v: query)
    vm.db = SimpleNamespace(open_table=lambda _n: table)
    return vm


def _search(vm, query):
    """search() with the embedding service pinned to a known model name."""
    from aja.memory import vector as vec_mod

    emb = SimpleNamespace(get_model_name=lambda: "test-model")
    with patch.object(vec_mod, "get_embedding_service", return_value=emb):
        return vm.search(query)


class TestVectorSearchMetadataGuard:
    def test_null_metadata_cell_does_not_kill_search(self):
        rows = [
            {"vector": [0.0], "text": "good", "metadata": None, "_distance": 0.1},
            {
                "vector": [0.0],
                "text": "stamped",
                "metadata": json.dumps({"embedding_model": "test-model"}),
                "_distance": 0.2,
            },
        ]
        vm = _fake_vector_memory(rows)
        results = _search(vm, [0.0])
        assert len(results) == 2
        # null metadata → treated as empty → legacy row stamped "unknown"
        assert results[0]["metadata"] == {"embedding_model": "unknown"}
        assert results[1]["metadata"]["embedding_model"] == "test-model"

    def test_malformed_metadata_cell_is_tolerated(self):
        rows = [
            {"text": "bad", "metadata": "{not json", "_distance": 0.3},
            {"text": "none-meta", "metadata": 42, "_distance": 0.4},
        ]
        vm = _fake_vector_memory(rows)
        results = _search(vm, [0.0])
        assert len(results) == 2
        # corrupt/non-string cells degrade to empty metadata, not a crash
        assert results[0]["metadata"] == {"embedding_model": "unknown"}
        assert results[1]["metadata"] == {"embedding_model": "unknown"}

    def test_all_null_cells_return_everything_as_legacy(self):
        rows = [
            {"text": f"r{i}", "metadata": None, "_distance": float(i)}
            for i in range(5)
        ]
        vm = _fake_vector_memory(rows)
        assert len(_search(vm, [0.0])) == 5


# ---------------------------------------------------------------------------
# T3#5 — skill_compiler None guards
# ---------------------------------------------------------------------------


class TestSkillCompilerNoneGuards:
    def _trajectory(self, domain=None, payloads=(None, "ls -la")):
        from aja.cognitive.memory_models import TrajectoryStep, TaskTrajectory

        steps = [
            TrajectoryStep(step_index=i, action_type="codeact", action_payload=p,
                           observation=None)
            for i, p in enumerate(payloads)
        ]
        return TaskTrajectory(goal="compile me", domain=domain, steps=steps)

    def test_none_domain_does_not_crash_generators(self, tmp_path):
        from aja.cognitive.skill_compiler import SkillCompiler

        compiler = SkillCompiler(skills_dir=tmp_path / "skills")
        traj = self._trajectory(domain=None)
        md = compiler._generate_skill_md("auto_test", traj)
        assert "general" in md
        script = compiler._generate_executable_script("auto_test", traj)
        compile(script, "run.py", "exec")

    def test_none_action_payload_slices_do_not_crash(self, tmp_path):
        from aja.cognitive.skill_compiler import SkillCompiler

        compiler = SkillCompiler(skills_dir=tmp_path / "skills")
        traj = self._trajectory(domain=None, payloads=(None,))
        md = compiler._generate_skill_md("auto_test", traj)
        assert "`None`" not in md.splitlines()[0]  # just ensure no exception above
        script = compiler._generate_executable_script("auto_test", traj)
        compile(script, "run.py", "exec")

    def test_explicit_domain_lowercased_in_tags(self, tmp_path):
        from aja.cognitive.skill_compiler import SkillCompiler

        compiler = SkillCompiler(skills_dir=tmp_path / "skills")
        traj = self._trajectory(domain="SysAdmin")
        compiler._generate_skill_md("auto_test", traj)
        compiler._generate_executable_script("auto_test", traj)
        # tags path exercised through full compile (validation gate runs too)
        result = compiler.distill_trajectory(traj)
        assert result is not None
        if result.skill_obj is not None:
            assert "sysadmin" in result.skill_obj.tags


# ---------------------------------------------------------------------------
# A4#1 — autonomous_loop try/finally cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def autoloop_env(monkeypatch):
    """Patches all collaborators of main_loop; records cleanup calls."""
    import aja.runtime.autonomous_loop as al
    import aja.runtime.single_instance as si

    calls = {"released": [], "intent_started": False, "intent_stopped": False}
    lock_sentinel = object()

    class FakeStore:
        def publish_heartbeat(self, *a, **k):
            return True

    monkeypatch.setattr(al, "LanceRuntimeStore", FakeStore)

    async def fake_heartbeats(memory, worker_id):
        while True:
            await asyncio.sleep(3600)

    monkeypatch.setattr(al, "publish_heartbeats", fake_heartbeats)

    import aja.autonomy.intent_engine as ie_mod

    class FakeIntentEngine:
        def start(self):
            calls["intent_started"] = True

        def stop(self):
            calls["intent_stopped"] = True

    fake_intent = FakeIntentEngine()
    monkeypatch.setattr(ie_mod, "intent_engine", fake_intent)

    import aja.goals.goal_engine as ge_mod

    fake_goal_engine = SimpleNamespace(
        get_active_goals=lambda: [],
        run_step=lambda: None,
    )
    monkeypatch.setattr(ge_mod, "goal_engine", fake_goal_engine)

    monkeypatch.setattr(si, "acquire_lock", lambda name: lock_sentinel)
    monkeypatch.setattr(
        si, "release_lock", lambda lock: calls["released"].append(lock)
    )

    return SimpleNamespace(module=al, calls=calls, lock=lock_sentinel)


class TestAutonomousLoopCleanup:
    def test_stop_event_release(self, autoloop_env):
        async def scenario():
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                autoloop_env.module.main_loop(stop_event)
            )
            await asyncio.sleep(0.2)
            stop_event.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(scenario())
        assert autoloop_env.calls["intent_started"]
        assert autoloop_env.calls["intent_stopped"]
        assert autoloop_env.calls["released"] == [autoloop_env.lock]

    def test_external_cancel_releases_lock_and_stops_heartbeat(self, autoloop_env):
        async def scenario():
            task = asyncio.create_task(autoloop_env.module.main_loop(None))
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # let pending callbacks settle
            await asyncio.sleep(0.05)

        asyncio.run(scenario())
        assert autoloop_env.calls["intent_started"]
        assert autoloop_env.calls["intent_stopped"]
        assert autoloop_env.calls["released"] == [autoloop_env.lock]

    def test_early_startup_failure_still_releases_lock(self, autoloop_env, monkeypatch):
        async def scenario():
            def boom():
                raise RuntimeError("lancedb down")

            monkeypatch.setattr(
                autoloop_env.module, "LanceRuntimeStore", boom
            )
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(
                    autoloop_env.module.main_loop(None), timeout=5
                )

        asyncio.run(scenario())
        # intent engine never started, but the lock MUST be released
        assert not autoloop_env.calls["intent_started"]
        assert not autoloop_env.calls["intent_stopped"]
        assert autoloop_env.calls["released"] == [autoloop_env.lock]

    def test_duplicate_lock_refusal_releases_nothing(self, autoloop_env, monkeypatch):
        import aja.runtime.single_instance as si

        monkeypatch.setattr(si, "acquire_lock", lambda name: None)
        asyncio.run(autoloop_env.module.main_loop(None))
        assert autoloop_env.calls["released"] == []
