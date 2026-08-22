"""
Priority Engine — multi-factor executive scoring for active tasks.

Extracted from api/bridge.py so the scoring logic is testable and reusable
independently of the FastAPI app.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from aja.memory.secretary import AJAMemory

STAKEHOLDER_WEIGHTS: dict[str, int] = {
    "recruiter": 90,
    "hiring manager": 95,
    "client": 85,
    "employer": 85,
    "manager": 80,
    "friend": 40,
    "personal": 30,
    "system": 20,
    "maintenance": 15,
    "default": 35,
}

CONSEQUENCE_MAP: dict[str, int] = {
    "urgent": 35,
    "high": 25,
    "medium": 15,
    "low": 5,
}

DELEGATION_RULES = {
    # Keywords in task title / description that suggest who should handle it
    "code": "Delegate to AJA Worker",
    "debug": "Delegate to AJA Worker",
    "fix bug": "Delegate to AJA Worker",
    "refactor": "Delegate to AJA Worker",
    "test": "Delegate to AJA Worker",
    "deploy": "Ask user first",
    "send": "Ask user first",
    "approve": "Ask user first",
    "email": "Ask user first",
    "reply": "Ask user first",
    "call": "Ask user first",
    "review": "Ask user first",
    "apply": "Do now — time-sensitive",
    "submit": "Do now — time-sensitive",
    "payment": "Do now — financial risk",
    "bill": "Do now — financial risk",
    "deadline": "Do now — deadline miss risk",
    "interview": "Do now — opportunity cost",
    "offer": "Do now — opportunity cost",
}


def _days_until(due_date_str: str | None) -> float | None:
    """Return signed days until due_date_str. Negative means overdue."""
    if not due_date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            due = datetime.strptime(due_date_str[:19], fmt[: len(due_date_str[:19])])
            return (due - datetime.now()).total_seconds() / 86400
        except ValueError:
            continue
    return None


def _stakeholder_weight(task: dict) -> int:
    title_lower = (task.get("title") or "").lower()
    desc_lower = (task.get("description") or "").lower()
    combined = f"{title_lower} {desc_lower}"
    for keyword, weight in sorted(STAKEHOLDER_WEIGHTS.items(), key=lambda x: -x[1]):
        if keyword in combined:
            return weight
    return STAKEHOLDER_WEIGHTS["default"]


def _delegation_recommendation(task: dict) -> str:
    title_lower = (task.get("title") or "").lower()
    desc_lower = (task.get("description") or "").lower()
    combined = f"{title_lower} {desc_lower}"
    for keyword, rec in DELEGATION_RULES.items():
        if keyword in combined:
            return rec
    priority = (task.get("priority") or "medium").lower()
    if priority in ("urgent", "high"):
        return "Do now"
    if priority == "medium":
        return "Follow up tomorrow"
    return "Archive — no real consequence"


def _urgency_challenge(days_left: float | None, priority: str) -> str | None:
    """Return a challenge message if the task's urgency may be inflated."""
    if days_left is None:
        return None
    if days_left > 5 and priority in ("low", "medium"):
        return "This feels urgent, but nothing breaks if it waits until tomorrow."
    if days_left > 14 and priority == "high":
        return "Deadline is 2+ weeks away. Safe to plan rather than act immediately."
    return None


def run_priority_engine(memory: "AJAMemory") -> Dict[str, Any]:
    """
    Score all active tasks using 5 dimensions:
      1. Urgency          — deadline proximity, overdue state, escalation age
      2. Stakeholder Weight — recruiter > client > friend > system
      3. Consequence      — financial, trust, opportunity, deadline risk
      4. Executive Intent — explicitly high-priority, repeated commitments
      5. Delegatability   — AJA / AJA Worker / operator

    Returns:
        top3        — top 3 tasks with full scoring metadata
        all_scored  — all tasks scored and sorted
        ignore_candidates — tasks safe to defer / archive this week
    """
    tasks = memory.list_tasks(
        statuses=["pending", "active", "blocked", "escalated"],
        include_archived=False,
        limit=100,
    )

    scored = []
    for task in tasks:
        priority = (task.get("priority") or "medium").lower()
        urgency_raw = task.get("urgency_score") or 0
        escalation = task.get("escalation_level") or 0
        days_left = _days_until(task.get("due_date"))
        stakeholder_w = _stakeholder_weight(task)
        consequence_w = CONSEQUENCE_MAP.get(priority, 15)

        # ── 1. Urgency score (0-40) ─────────────────────────────────────────
        urgency_pts = 0
        if days_left is not None:
            if days_left < 0:  # overdue
                urgency_pts = 40
            elif days_left < 1:  # due today
                urgency_pts = 35
            elif days_left < 3:  # due very soon
                urgency_pts = 25
            elif days_left < 7:
                urgency_pts = 15
            else:
                urgency_pts = max(0, 10 - int(days_left / 3))
        else:
            # No due date — rely on raw urgency_score
            urgency_pts = min(40, int(urgency_raw * 0.4))

        # Escalation age bonus
        urgency_pts = min(40, urgency_pts + escalation * 5)

        # ── 2. Stakeholder weight (0-30) ────────────────────────────────────
        stakeholder_pts = int(stakeholder_w * 0.3)  # scale 0-95 → 0-28

        # ── 3. Consequence of delay (0-20) ──────────────────────────────────
        consequence_pts = min(20, consequence_w)

        # ── 4. Executive Intent bonus (0-10) ────────────────────────────────
        intent_pts = 0
        if priority == "urgent":
            intent_pts = 10
        elif priority == "high":
            intent_pts = 6
        elif escalation >= 2:
            intent_pts = 8

        # ── Composite priority_score (0-100) ────────────────────────────────
        priority_score = min(
            100, urgency_pts + stakeholder_pts + consequence_pts + intent_pts
        )

        # ── Urgency tier ────────────────────────────────────────────────────
        if priority_score >= 80:
            tier = "critical"
        elif priority_score >= 60:
            tier = "high"
        elif priority_score >= 35:
            tier = "medium"
        else:
            tier = "low"

        # ── Delegation recommendation ────────────────────────────────────────
        delegation_rec = _delegation_recommendation(task)

        # ── Escalation recommendation ────────────────────────────────────────
        should_escalate = escalation < 2 and (
            days_left is not None and days_left < 0 or priority_score >= 80
        )
        escalation_rec = (
            "Escalate now — overdue or critical"
            if should_escalate
            else ("Monitor" if priority_score >= 50 else "No escalation needed")
        )

        # ── Ignore / Archive suggestion ─────────────────────────────────────
        can_ignore = priority_score < 25 and (days_left is None or days_left > 7)
        ignore_reason = None
        if can_ignore:
            if days_left and days_left > 14:
                ignore_reason = (
                    f"Due in {int(days_left)} days — low urgency, no stakeholder risk."
                )
            else:
                ignore_reason = (
                    "Low priority, no deadline pressure, no stakeholder consequence."
                )

        # ── Approval recommendation ──────────────────────────────────────────
        approval_rec = (
            "Approve immediately"
            if priority_score >= 80
            else "Review before acting"
            if priority_score >= 50
            else "No approval needed — low risk"
        )

        # ── Urgency challenge ────────────────────────────────────────────────
        challenge = _urgency_challenge(days_left, priority)

        scored.append(
            {
                **task,
                "priority_score": priority_score,
                "urgency_tier": tier,
                "urgency_pts": urgency_pts,
                "stakeholder_pts": stakeholder_pts,
                "consequence_pts": consequence_pts,
                "intent_pts": intent_pts,
                "decision_recommendation": delegation_rec,
                "escalation_recommendation": escalation_rec,
                "can_ignore": can_ignore,
                "ignore_reason": ignore_reason,
                "approval_recommendation": approval_rec,
                "urgency_challenge": challenge,
                "days_until_due": round(days_left, 1)
                if days_left is not None
                else None,
            }
        )

    scored.sort(key=lambda t: -t["priority_score"])
    top3 = scored[:3]
    ignore_candidates = [t for t in scored if t["can_ignore"]]

    return {
        "top3": top3,
        "all_scored": scored,
        "ignore_candidates": ignore_candidates,
        "total_tasks": len(scored),
    }
