"""
AJA Evals: Scoring Engine
=========================
Scores mission journal event streams against an EvalCase rubric.

Score math: every assertion is worth 1/n of the total score; each failed
assertion subtracts 1/n. If TOOL_FAILED events are present and TOOL_FAILED
was not explicitly expected (i.e. not in forbidden_event_types as an
"expected failure" allowance via required/rubric), the score is capped at 0.5.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from aja.evals.case import EvalCase

# Cap applied when TOOL_FAILED appears but was not declared expected.
TOOL_FAILURE_SCORE_CAP = 0.5


@dataclass
class EvalResult:
    name: str
    passed: bool
    score: float
    failures: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "score": round(self.score, 4),
            "failures": list(self.failures),
        }


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(str(value))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return ts
    except (TypeError, ValueError):
        return None


def _event_text(event: Dict[str, Any]) -> str:
    """Flattened searchable text of a single event payload."""
    try:
        return json.dumps(event, default=str)
    except (TypeError, ValueError):
        return str(event)


def _duration_ms(events: List[Dict[str, Any]]) -> Optional[float]:
    stamps = [_parse_ts(e.get("timestamp")) for e in events]
    stamps = [s for s in stamps if s is not None]
    if len(stamps) < 2:
        return None
    return (max(stamps) - min(stamps)).total_seconds() * 1000.0


def _eval_declarative(item: Dict[str, Any], events: List[Dict[str, Any]]) -> Optional[str]:
    """
    Evaluates one declarative rubric item.
    Returns a failure detail string, or None when the assertion holds.
    """
    kind = item.get("type")

    if kind == "event_present":
        etype = item.get("event_type")
        where = item.get("where", "any")
        matching = [e for e in events if e.get("event_type") == etype]
        if not matching:
            return f"event_present: no '{etype}' event found"
        if where == "last":
            if events[-1].get("event_type") != etype:
                return f"event_present: last event is not '{etype}'"
        return None

    if kind == "tool_succeeded":
        tool = item.get("tool")
        ok = any(
            e.get("event_type") == "TOOL_COMPLETED"
            and (tool is None or e.get("tool") == tool)
            and (
                e.get("success") is True
                or e.get("exit_code") == 0
                or e.get("success") is None
                and e.get("exit_code") is None
            )
            for e in events
        )
        if not ok:
            label = tool if tool is not None else "any tool"
            return f"tool_succeeded: no successful TOOL_COMPLETED for '{label}'"
        return None

    if kind == "max_duration_ms":
        limit = item.get("value")
        dur = _duration_ms(events)
        if dur is None:
            return "max_duration_ms: fewer than 2 parseable timestamps"
        if dur > float(limit):
            return f"max_duration_ms: took {dur:.0f}ms > budget {limit}ms"
        return None

    if kind == "output_contains":
        text = str(item.get("text", ""))
        haystack = "\n".join(_event_text(e) for e in events)
        if text not in haystack:
            return f"output_contains: '{text}' not found in any event payload"
        return None

    return f"unknown rubric type: {kind!r}"


def score_events(case: EvalCase, events: List[Dict[str, Any]]) -> EvalResult:
    """
    Scores an event stream against an EvalCase.

    Each failed assertion costs 1/n of the total score (n = assertion count).
    TOOL_FAILED present without explicit expectation caps the score at 0.5.
    """
    failures: List[str] = []
    assertions = 0

    def check(ok: bool, detail: str) -> None:
        nonlocal assertions
        assertions += 1
        if not ok:
            failures.append(detail)

    # Required event types must appear at least once.
    present = {e.get("event_type") for e in events}
    for etype in case.required_event_types:
        check(etype in present, f"required_event_type missing: {etype}")

    # Forbidden event types must not appear.
    for etype in case.forbidden_event_types:
        check(etype not in present, f"forbidden_event_type present: {etype}")

    # Rubric items (callables or declarative dicts).
    for item in case.rubric:
        if callable(item):
            try:
                outcome = item(events)
            except Exception as exc:
                check(False, f"rubric callable raised: {exc}")
                continue
            if isinstance(outcome, tuple):
                ok, detail = bool(outcome[0]), str(outcome[1])
            else:
                ok, detail = bool(outcome), "rubric callable returned False"
            check(ok, detail)
        elif isinstance(item, dict):
            detail = _eval_declarative(item, events)
            assertions += 1
            if detail is not None:
                failures.append(detail)
        else:
            check(False, f"invalid rubric item: {item!r}")

    # Latency budget (case-level).
    if case.latency_budget_ms is not None:
        dur = _duration_ms(events)
        ok = dur is not None and dur <= float(case.latency_budget_ms)
        detail = (
            f"latency_budget_ms: took {dur:.0f}ms > budget {case.latency_budget_ms}ms"
            if dur is not None
            else "latency_budget_ms: cannot measure duration (<2 timestamps)"
        )
        check(ok, detail)

    n = max(assertions, 1)
    score = 1.0 - (len(failures) / n)
    score = max(0.0, min(1.0, score))

    # Automatic penalty cap: unexpected tool failures halve confidence.
    # A case that explicitly REQUIRES TOOL_FAILED declares failures as
    # expected (e.g. chaos/fault-injection cases) and is exempt.
    expected_failure = "TOOL_FAILED" in case.required_event_types
    if "TOOL_FAILED" in present and not expected_failure:
        score = min(score, TOOL_FAILURE_SCORE_CAP)
        failures.append(
            "TOOL_FAILED events present (score capped at "
            f"{TOOL_FAILURE_SCORE_CAP})"
        )

    return EvalResult(
        name=case.name,
        passed=(len(failures) == 0),
        score=round(score, 4),
        failures=failures,
    )
