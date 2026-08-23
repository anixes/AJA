"""
AJA Evals: Runner & Regression Gate
===================================
Locates mission event streams (live MissionJournal or replay artifacts),
scores them against EvalCases, and runs baseline regression gates.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from aja.evals.case import EvalCase, get_case
from aja.evals.scoring import EvalResult, score_events

logger = logging.getLogger(__name__)

# Missions scoring below their stored baseline by more than this delta fail the gate.
REGRESSION_TOLERANCE = 0.2


@dataclass
class GateEntry:
    mission_id: str
    score: float
    baseline_score: Optional[float]
    status: str  # "pass" | "regression" | "new" | "error"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "score": round(self.score, 4),
            "baseline_score": (
                None if self.baseline_score is None else round(self.baseline_score, 4)
            ),
            "status": self.status,
        }


@dataclass
class GateReport:
    passed: bool
    entries: List[GateEntry] = field(default_factory=list)
    baseline_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "baseline_path": self.baseline_path,
            "entries": [e.to_dict() for e in self.entries],
        }


def _load_journal_events(
    mission_id: str,
    journals_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Reads a mission's events via MissionJournal, optionally redirecting the
    journal directory (used by tests and offline analysis).
    """
    from aja.runtime.mission_journal import MissionJournal

    journal = MissionJournal(mission_id)
    if journals_dir is not None:
        journals_dir = Path(journals_dir)
        journal.journal_dir = journals_dir
        journal.journal_path = journals_dir / f"mission_{mission_id}.jsonl"
    return journal.read_events()


def _load_replay_events(
    session_id: str,
    executions_dir: Path,
) -> List[Dict[str, Any]]:
    """Reads replay timeline events via SessionReplayDataLoader."""
    from aja.runtime.replay_engine import SessionReplayDataLoader

    loader = SessionReplayDataLoader(session_id=session_id, executions_dir=Path(executions_dir))
    return loader.get_events()


def run_case(
    case: Union[str, EvalCase, None] = None,
    mission_id: Optional[str] = None,
    session_id: Optional[str] = None,
    journals_dir: Optional[Path] = None,
    executions_dir: Optional[Path] = None,
) -> EvalResult:
    """
    Scores one EvalCase against a mission's events.

    Resolution order:
      1. session_id given -> replay-mode via SessionReplayDataLoader.
      2. mission_id given -> live MissionJournal events.
      3. Neither -> EvalResult with score 0.0.

    ``case`` may be an EvalCase instance or a BUILTIN_CASES name
    (defaults to "clean_run" when omitted).
    """
    resolved: EvalCase = get_case(case if case is not None else "clean_run")
    if mission_id is None and session_id is None:
        raise ValueError("run_case requires mission_id or session_id")

    if session_id is not None:
        from aja.config import DATA_DIR

        exec_dir = Path(executions_dir) if executions_dir else DATA_DIR / "executions"
        events = _load_replay_events(session_id, exec_dir)
    elif mission_id is not None:
        events = _load_journal_events(mission_id, journals_dir)
    else:
        events = []

    if not events:
        return EvalResult(
            name=resolved.name,
            passed=False,
            score=0.0,
            failures=["no events found to evaluate"],
        )
    return score_events(resolved, events)


def run_regression_gate(
    baseline_path: Path,
    top_n: int = 10,
    missions_dir: Optional[Path] = None,
    case_name: str = "clean_run",
) -> GateReport:
    """
    Scores the N most recent missions against a stored baseline.

    Baseline JSON shape: {mission_key: score}. Any mission scoring below its
    baseline by more than REGRESSION_TOLERANCE fails the gate. The baseline is
    rewritten with the fresh scores afterwards.
    """
    from aja.config import DATA_DIR

    baseline_path = Path(baseline_path)
    mdir = Path(missions_dir) if missions_dir else DATA_DIR / "missions"

    baseline: Dict[str, float] = {}
    if baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not read baseline %s (%s); starting fresh", baseline_path, e)
            baseline = {}

    # Most recent non-shard mission journals by mtime.
    shard_marker = "_shard_"
    journals = [
        p
        for p in mdir.glob("mission_*.jsonl")
        if shard_marker not in p.stem
    ]
    journals.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    case = get_case(case_name)
    entries: List[GateEntry] = []
    new_baseline: Dict[str, float] = {}

    for path in journals[: max(1, top_n)]:
        mission_id = path.stem.replace("mission_", "", 1)
        try:
            events = _load_journal_events(mission_id, journals_dir=mdir)
            result = score_events(case, events)
        except Exception as e:
            entries.append(GateEntry(mission_id, 0.0, baseline.get(mission_id), "error"))
            logger.warning("Failed scoring mission %s: %s", mission_id, e)
            continue

        new_baseline[mission_id] = result.score
        base = baseline.get(mission_id)
        if base is None:
            status = "new"
        elif result.score < float(base) - REGRESSION_TOLERANCE:
            status = "regression"
        else:
            status = "pass"
        entries.append(GateEntry(mission_id, result.score, base, status))

    passed = all(e.status != "regression" for e in entries)

    # Persist updated baseline (atomic-ish write).
    try:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(new_baseline, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write baseline %s: %s", baseline_path, e)

    return GateReport(passed=passed, entries=entries, baseline_path=str(baseline_path))
