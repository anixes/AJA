"""
Worker Recommendation Engine — scores and ranks available workers against a task.

Extracted from api/bridge.py so the matching logic is testable and reusable
independently of the FastAPI app.
"""

from typing import TYPE_CHECKING, Any, Dict

from aja.api.services.priority_engine import _days_until

if TYPE_CHECKING:
    from aja.memory.secretary import AJAMemory

_TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "code": [
        "code",
        "implement",
        "build",
        "create",
        "feature",
        "develop",
        "write code",
        "add",
    ],
    "fix": [
        "fix",
        "bug",
        "error",
        "broken",
        "issue",
        "debug",
        "patch",
        "repair",
        "crash",
    ],
    "refactor": [
        "refactor",
        "restructure",
        "clean",
        "reorganize",
        "simplify",
        "modularize",
    ],
    "test": [
        "test",
        "spec",
        "coverage",
        "unit test",
        "e2e",
        "integration test",
        "assert",
        "tdd",
    ],
    "deploy": [
        "deploy",
        "release",
        "ci/cd",
        "pipeline",
        "production",
        "staging",
        "publish",
    ],
    "review": ["review", "audit", "inspect", "check", "evaluate", "pr review"],
    "research": [
        "research",
        "investigate",
        "analyze",
        "explore",
        "study",
        "compare",
        "evaluate",
    ],
    "documentation": [
        "document",
        "readme",
        "docs",
        "wiki",
        "explain",
        "api doc",
        "specification",
    ],
    "git": ["git", "commit", "branch", "merge", "rebase", "cherry-pick", "stash"],
    "pr": ["pull request", "pr", "merge request"],
    "maintenance": ["maintain", "health", "monitor", "clean up", "optimize", "repair"],
    "analysis": ["analyze", "analyse", "report", "metric", "dashboard", "data"],
}

_SPEED_MAP = {"fast": 3, "medium": 2, "slow": 1}


def _infer_task_type(objective: str) -> list[str]:
    """Infer task type tags from a free-text objective."""
    objective_lower = objective.lower()
    matched: list[str] = []
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in objective_lower:
                matched.append(task_type)
                break
    return matched or ["code"]  # default to 'code' if nothing matched


def _extract_risk_level(task: dict | None) -> str:
    """Determine the risk level from a task's priority or explicit risk field."""
    if not task:
        return "medium"
    risk = (task.get("approval_risk_level") or "").lower()
    if risk in ("high", "critical"):
        return "high"
    priority = (task.get("priority") or "medium").lower()
    if priority in ("urgent", "high"):
        return "high"
    if priority == "low":
        return "low"
    return "medium"


def _extract_speed_need(task: dict | None) -> str:
    """Determine how fast we need the worker to be."""
    if not task:
        return "medium"
    days_left = _days_until((task or {}).get("due_date"))
    if days_left is not None and days_left < 1:
        return "fast"
    if days_left is not None and days_left < 3:
        return "fast"
    priority = (task.get("priority") or "medium").lower()
    if priority in ("urgent",):
        return "fast"
    return "medium"


def recommend_workers_for_task(
    memory: "AJAMemory",
    objective: str,
    task: dict | None = None,
    require_tests: bool = False,
    require_git: bool = False,
    require_deploy: bool = False,
) -> Dict[str, Any]:
    """
    Score and rank all available workers against a task objective.

    Returns:
        recommended  — ranked list of worker recommendations with scores + reasons
        analysis     — task analysis summary
        cautions     — any warnings for the operator
    """
    workers = memory.list_workers(status="available", limit=50)
    if not workers:
        all_workers = memory.list_workers(limit=50)
        return {
            "recommended": [],
            "analysis": {
                "objective": objective,
                "inferred_types": _infer_task_type(objective),
                "risk_level": _extract_risk_level(task),
                "speed_need": _extract_speed_need(task),
            },
            "cautions": [
                f"No available workers found. {len(all_workers)} worker(s) registered but none with availability_status='available'."
            ],
            "total_workers": len(all_workers),
        }

    inferred_types = _infer_task_type(objective)
    risk_level = _extract_risk_level(task)
    speed_need = _extract_speed_need(task)

    recommendations = []
    for worker in workers:
        score = 0.0
        reasons: list[str] = []
        cautions: list[str] = []

        # ── 1. Task type match (0-30) ─────────────────────────────────────────
        preferred = set(worker.get("preferred_task_types") or [])
        blocked = set(worker.get("blocked_task_types") or [])
        matched_types = set(inferred_types) & preferred
        blocked_types = set(inferred_types) & blocked

        if blocked_types:
            cautions.append(f"Blocked for: {', '.join(blocked_types)}")
            score -= 50  # heavy penalty, but keep in list for transparency
        if matched_types:
            type_score = min(30, len(matched_types) * 10)
            score += type_score
            reasons.append(f"Matches task types: {', '.join(matched_types)}")
        elif not blocked_types:
            score += 5
            reasons.append("General-purpose worker (no specific type match)")

        # ── 2. Capability requirements (0-15) ────────────────────────────────
        cap_score = 0
        if require_tests and worker.get("supports_tests"):
            cap_score += 5
            reasons.append("Supports test execution")
        elif require_tests and not worker.get("supports_tests"):
            cautions.append("Does NOT support tests (required)")
            score -= 10
        if require_git and worker.get("supports_git_operations"):
            cap_score += 5
            reasons.append("Supports git operations")
        elif require_git and not worker.get("supports_git_operations"):
            cautions.append("Does NOT support git operations (required)")
            score -= 10
        if require_deploy and worker.get("supports_deployment"):
            cap_score += 5
            reasons.append("Supports deployment")
        elif require_deploy and not worker.get("supports_deployment"):
            cautions.append("Does NOT support deployment (required)")
            score -= 15
        score += cap_score

        # ── 3. Reliability (0-25) ────────────────────────────────────────────
        reliability = worker.get("reliability_score", 0.5)
        reliability_pts = reliability * 25
        score += reliability_pts
        if reliability >= 0.9:
            reasons.append(f"High reliability ({reliability:.0%})")
        elif reliability < 0.7:
            cautions.append(f"Low reliability ({reliability:.0%})")

        # ── 4. Speed match (0-15) ────────────────────────────────────────────
        worker_speed = _SPEED_MAP.get(worker.get("execution_speed", "medium"), 2)
        needed_speed = _SPEED_MAP.get(speed_need, 2)
        if worker_speed >= needed_speed:
            speed_pts = min(15, worker_speed * 5)
            score += speed_pts
            if speed_need == "fast" and worker_speed >= 3:
                reasons.append("Fast execution — matches urgency")
        else:
            score += max(0, worker_speed * 3)
            if speed_need == "fast":
                cautions.append("Slower than ideal for this urgent task")

        # ── 5. Cost profile (0-10) ───────────────────────────────────────────
        cost = worker.get("cost_profile", "subscription")
        if cost == "free":
            score += 10
            reasons.append("Zero-cost execution")
        elif cost == "subscription":
            score += 7
            reasons.append("Subscription-based (no per-task cost)")
        else:
            score += 3
            cautions.append("Pay-per-use — may incur additional costs")

        # ── 6. Risk alignment (0-5) ──────────────────────────────────────────
        worker_risk = worker.get("approval_risk_level", "medium")
        if risk_level == "high" and worker_risk == "low":
            score += 5
            reasons.append("Low-risk worker for high-risk task — safer choice")
        elif risk_level == "low":
            score += 3

        # ── 7. Historical performance bonus (0-10) ──────────────────────────
        success_rate = worker.get("historical_success_rate", 0)
        total_executed = worker.get("total_tasks_executed", 0)
        if total_executed >= 5:
            perf_pts = min(10, success_rate / 10)
            score += perf_pts
            if success_rate >= 90:
                reasons.append(
                    f"Proven track record ({success_rate:.0f}% success over {total_executed} tasks)"
                )
            elif success_rate < 60:
                cautions.append(f"Low success rate ({success_rate:.0f}%)")

        # ── 8. Strength keyword overlap (0-5) bonus ──────────────────────────
        strengths = " ".join(worker.get("primary_strengths") or []).lower()
        objective_lower = objective.lower()
        strength_hits = sum(
            1 for word in objective_lower.split() if word in strengths and len(word) > 3
        )
        if strength_hits > 0:
            score += min(5, strength_hits * 2)
            reasons.append(f"Strength keywords overlap ({strength_hits} matches)")

        # Final composite
        final_score = max(0, min(100, score))

        recommendations.append(
            {
                "worker_id": worker["worker_id"],
                "worker_name": worker["worker_name"],
                "worker_type": worker["worker_type"],
                "recommendation_score": round(final_score, 1),
                "reasons": reasons,
                "cautions": cautions,
                "execution_speed": worker.get("execution_speed"),
                "reliability_score": worker.get("reliability_score"),
                "cost_profile": worker.get("cost_profile"),
                "supports_tests": worker.get("supports_tests"),
                "supports_git": worker.get("supports_git_operations"),
                "supports_deploy": worker.get("supports_deployment"),
                "recent_failures": (worker.get("recent_failures") or [])[:3],
            }
        )

    recommendations.sort(key=lambda r: -r["recommendation_score"])

    # Top recommendation advisory
    top = recommendations[0] if recommendations else None
    advisory: list[str] = []
    if top and top["cautions"]:
        advisory.append(
            f"Top pick ({top['worker_name']}) has cautions: {'; '.join(top['cautions'])}"
        )
    if len(recommendations) >= 2:
        gap = (
            recommendations[0]["recommendation_score"]
            - recommendations[1]["recommendation_score"]
        )
        if gap < 5:
            advisory.append(
                f"Close match between {recommendations[0]['worker_name']} and {recommendations[1]['worker_name']} "
                f"(gap: {gap:.1f}pts) — your judgment matters here."
            )

    return {
        "recommended": recommendations,
        "analysis": {
            "objective": objective,
            "inferred_types": inferred_types,
            "risk_level": risk_level,
            "speed_need": speed_need,
            "require_tests": require_tests,
            "require_git": require_git,
            "require_deploy": require_deploy,
        },
        "cautions": advisory,
        "total_workers": len(workers),
    }
