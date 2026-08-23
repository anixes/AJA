"""
Tests for the replay-based evaluation framework (aja.evals).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from aja.evals.case import BUILTIN_CASES, EvalCase, get_case
from aja.evals.runner import run_case, run_regression_gate
from aja.evals.scoring import EvalResult, score_events

BASE_TS = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _ts(offset_s):
    return (BASE_TS + timedelta(seconds=offset_s)).isoformat()


def make_event(etype, mission_id="m1", seq=0, offset=0.0, **payload):
    return {
        "event_type": etype,
        "event_schema_version": "1.0",
        "mission_id": mission_id,
        "sequence": seq,
        "timestamp": _ts(offset),
        **payload,
    }


def write_journal(journals_dir, mission_id, events):
    journals_dir.mkdir(parents=True, exist_ok=True)
    path = journals_dir / f"mission_{mission_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


PERFECT_RUN = [
    make_event("MISSION_CREATED", goal="do thing", offset=0),
    make_event("MISSION_STATUS_CHANGED", to="ACTIVE", offset=1),
    make_event("MISSION_RUN_STARTED", run_id="r1", offset=2),
    make_event("MISSION_PLAN_GENERATED", plan_id="p1", offset=3),
    make_event("TOOL_CALLED", tool="search_web", offset=4),
    make_event("TOOL_COMPLETED", tool="search_web", success=True, exit_code=0, offset=5),
    make_event("MISSION_COMPLETED", success=True, result_summary="all done", offset=6),
]

FAILURE_RUN = [
    make_event("MISSION_CREATED", goal="do thing", offset=0),
    make_event("MISSION_RUN_STARTED", run_id="r1", offset=1),
    make_event("TOOL_CALLED", tool="shell", offset=2),
    make_event("TOOL_FAILED", tool="shell", error="boom", offset=3),
    make_event("TOOL_COMPLETED", tool="shell", success=False, exit_code=1, offset=4),
    make_event("MISSION_COMPLETED", success=True, result_summary="recovered", offset=5),
]


@pytest.fixture
def journals_dir(tmp_path):
    return tmp_path / "missions"


class TestScoring:
    def test_perfect_run_scores_one(self):
        result = score_events(get_case("planned_run"), PERFECT_RUN)
        assert isinstance(result, EvalResult)
        assert result.passed is True
        assert result.score == 1.0
        assert result.failures == []

    def test_missing_required_event_fails(self):
        events = [e for e in PERFECT_RUN if e["event_type"] != "MISSION_PLAN_GENERATED"]
        result = score_events(get_case("planned_run"), events)
        assert not result.passed
        # 4 required + 1 forbidden assertion; 1 failure -> 0.8.
        assert result.score == pytest.approx(0.8)
        assert any("MISSION_PLAN_GENERATED" in f for f in result.failures)

    def test_forbidden_tool_failed_caps_score(self):
        # clean_run forbids TOOL_FAILED: absence assertion fails (1/4) -> 0.75,
        # then unexpected-failure cap clamps to 0.5.
        result = score_events(get_case("clean_run"), FAILURE_RUN)
        assert not result.passed
        assert result.score == pytest.approx(0.5)

    def test_expected_failure_not_capped(self):
        case = EvalCase(
            name="chaos",
            objective="failure is expected",
            required_event_types=["MISSION_CREATED", "TOOL_FAILED", "MISSION_COMPLETED"],
        )
        result = score_events(case, FAILURE_RUN)
        assert result.passed
        assert result.score == 1.0

    def test_declarative_rubric_types(self):
        case = EvalCase(
            name="rich",
            objective="all rubric kinds",
            rubric=[
                {"type": "event_present", "event_type": "TOOL_CALLED"},
                {"type": "tool_succeeded", "tool": "search_web"},
                {"type": "max_duration_ms", "value": 10000},
                {"type": "output_contains", "text": "all done"},
            ],
            latency_budget_ms=20000,
        )
        result = score_events(case, PERFECT_RUN)
        assert result.passed, result.failures
        assert result.score == 1.0

    def test_output_contains_miss_scores_zero(self):
        case = EvalCase(
            name="contains",
            rubric=[{"type": "output_contains", "text": "nonexistent-token"}],
        )
        result = score_events(case, PERFECT_RUN)
        assert not result.passed
        assert result.score == pytest.approx(0.0)

    def test_callable_rubric(self):
        case = EvalCase(
            name="callable",
            rubric=[
                lambda events: sum(
                    1 for e in events if e["event_type"] == "TOOL_COMPLETED"
                )
                >= 1
            ],
        )
        assert score_events(case, PERFECT_RUN).passed
        assert not score_events(case, [make_event("MISSION_CREATED")]).passed

    def test_latency_budget_exceeded(self):
        case = EvalCase(name="slow", latency_budget_ms=500)
        result = score_events(case, PERFECT_RUN)
        assert not result.passed
        assert result.score == 0.0

    def test_empty_events_zero(self):
        result = score_events(get_case("clean_run"), [])
        assert not result.passed
        # 3 missing required out of 4 assertions -> 0.25.
        assert result.score == pytest.approx(0.25)
        assert any("MISSION_CREATED" in f for f in result.failures)


class TestRunner:
    def test_run_case_against_journal_dir(self, journals_dir):
        write_journal(journals_dir, "perfect", PERFECT_RUN)
        result = run_case("planned_run", mission_id="perfect", journals_dir=journals_dir)
        assert result.passed
        assert result.score == 1.0

    def test_run_case_detects_failures(self, journals_dir):
        write_journal(journals_dir, "broken", FAILURE_RUN)
        result = run_case("clean_run", mission_id="broken", journals_dir=journals_dir)
        assert not result.passed
        assert result.score <= 0.5

    def test_run_case_no_events(self, journals_dir):
        result = run_case("clean_run", mission_id="ghost", journals_dir=journals_dir)
        assert result.score == 0.0
        assert "no events found" in result.failures[0]

    def test_run_case_requires_target(self):
        with pytest.raises(ValueError):
            run_case("clean_run")

    def test_run_case_replay_mode(self, tmp_path):
        exec_dir = tmp_path / "executions"
        session_dir = exec_dir / "sess-42"
        session_dir.mkdir(parents=True)
        with (session_dir / "timeline.jsonl").open("w", encoding="utf-8") as f:
            for e in PERFECT_RUN:
                f.write(json.dumps(e) + "\n")
        (session_dir / "manifest.json").write_text(json.dumps({"session_id": "sess-42"}))
        result = run_case("planned_run", session_id="sess-42", executions_dir=exec_dir)
        assert result.passed
        assert result.score == 1.0


class TestRegressionGate:
    def _seed(self, missions_dir, mapping):
        for mid, events in mapping.items():
            write_journal(missions_dir, mid, events)

    def test_gate_pass_and_baseline_write(self, tmp_path):
        missions_dir = tmp_path / "missions"
        self._seed(missions_dir, {"good1": PERFECT_RUN, "good2": PERFECT_RUN})
        baseline_path = tmp_path / "baseline.json"

        report = run_regression_gate(baseline_path, top_n=10, missions_dir=missions_dir)

        assert report.passed
        statuses = {e.mission_id: e.status for e in report.entries}
        assert statuses == {"good1": "new", "good2": "new"}
        stored = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert stored == {"good1": 1.0, "good2": 1.0}

    def test_gate_fails_on_regression(self, tmp_path):
        missions_dir = tmp_path / "missions"
        self._seed(missions_dir, {"regressed": FAILURE_RUN, "stable": PERFECT_RUN})
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps({"regressed": 1.0, "stable": 1.0}))

        report = run_regression_gate(baseline_path, top_n=10, missions_dir=missions_dir)

        assert not report.passed
        by_id = {e.mission_id: e for e in report.entries}
        assert by_id["regressed"].status == "regression"
        assert by_id["regressed"].score <= 0.5
        assert by_id["stable"].status == "pass"

    def test_gate_within_tolerance_passes(self, tmp_path):
        # Mild run scores 1.0 vs baseline 1.15-equivalent delta check:
        # baseline 1.0, score 1.0 -> pass; also verify >0.2 drop fails elsewhere.
        missions_dir = tmp_path / "missions"
        mild = [
            make_event("MISSION_CREATED", offset=0),
            make_event("MISSION_RUN_STARTED", offset=1),
            make_event("MISSION_COMPLETED", offset=2),
        ]
        self._seed(missions_dir, {"mild": mild})
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps({"mild": 0.95}))

        report = run_regression_gate(baseline_path, missions_dir=missions_dir)

        assert report.entries[0].status == "pass"
        assert report.passed

    def test_gate_skips_shard_files(self, tmp_path):
        missions_dir = tmp_path / "missions"
        self._seed(missions_dir, {"only": PERFECT_RUN})
        # Shard file belongs to another mission's journal, must be ignored.
        write_journal(missions_dir, "only_shard_0", [make_event("MISSION_CREATED")])

        report = run_regression_gate(tmp_path / "baseline.json", missions_dir=missions_dir)

        assert [e.mission_id for e in report.entries] == ["only"]

    def test_gate_top_n_limits_scope(self, tmp_path):
        missions_dir = tmp_path / "missions"
        self._seed(
            missions_dir,
            {f"m{i}": PERFECT_RUN for i in range(5)},
        )
        report = run_regression_gate(
            tmp_path / "baseline.json", top_n=2, missions_dir=missions_dir
        )
        assert len(report.entries) == 2


class TestCliSmoke:
    def test_cmd_eval_list_runs(self, capsys):
        from aja.cli.commands.eval_cmd import cmd_eval

        cmd_eval(mode="list")
        out = capsys.readouterr().out
        assert "clean_run" in out

    def test_cmd_eval_unknown_case_exits(self):
        from aja.cli.commands.eval_cmd import cmd_eval

        with pytest.raises(SystemExit):
            cmd_eval(mode="run", case="nope", mission_id="whatever")

    def test_cmd_eval_run_needs_mission(self):
        from aja.cli.commands.eval_cmd import cmd_eval

        with pytest.raises(SystemExit):
            cmd_eval(mode="run", case="clean_run")

    def test_cmd_eval_run_live_data_dir(self):
        # Exercises the full default path through MissionJournal + DATA_DIR.
        from pathlib import Path

        from aja.cli.commands.eval_cmd import cmd_eval
        from aja.config import DATA_DIR

        mid = "evalsmoke_test_only"
        write_journal(DATA_DIR / "missions", mid, PERFECT_RUN)
        try:
            cmd_eval(mode="run", case="planned_run", mission_id=mid)
        finally:
            p = DATA_DIR / "missions" / f"mission_{mid}.jsonl"
            if p.exists():
                p.unlink()

    def test_builtin_cases_exist(self):
        assert "clean_run" in BUILTIN_CASES
        assert "planned_run" in BUILTIN_CASES
