"""
aja.goals.analyzer -- SMART Goal Analysis and Progress Tracking
==============================================================
Provides SMART goal validation, habit tracking, velocity estimation,
and progress evaluation according to the Goal-Analyzer specification.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SmartValidationResult:
    goal: str
    smart_scores: Dict[str, float]  # specific, measurable, achievable, relevant, time_bound (1.0 to 5.0)
    overall_score: float
    grade: str  # S, A, B, C
    assessment: str
    suggestions: List[str] = field(default_factory=list)
    milestones: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProgressResult:
    current_value: float
    target_value: float
    start_value: float
    completion_percentage: float
    time_percentage: float
    velocity: float
    status: str  # "ahead", "on_track", "behind", "critically_behind"
    estimated_days_remaining: Optional[float]
    assessment: str
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HabitResult:
    habit_name: str
    current_streak: int
    longest_streak: int
    total_days: int
    completed_days: int
    completion_rate: float
    strength_score: float  # 1.0 to 10.0
    stage: str  # Ignition, Formation, Consolidation, Habit, Automated
    next_milestone: int
    assessment: str
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GoalAnalyzer:
    """Evaluates objectives against SMART criteria and tracks progress / habits."""

    TIME_UNITS = r"(?:day|days|week|weeks|month|months|year|years|hour|hours|min|minutes|s|sec|seconds)"
    NUM_PATTERN = r"(?:\d+(?:\.\d+)?)"

    @classmethod
    def validate_smart(
        cls,
        objective: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SmartValidationResult:
        """
        Validate an objective against SMART criteria:
        - Specific: Clear action verb, defined target, lack of ambiguity.
        - Measurable: Quantifiable metric (numbers, units, counts, percentages).
        - Achievable: Realistic range and reasonable scope.
        - Relevant: Meaningful outcome or context alignment.
        - Time-bound: Explicit deadline, duration, or cadence.
        """
        text = objective.strip()
        context = context or {}

        # 1. Specific
        score_s = 2.0
        s_suggestions = []
        action_verbs = [
            "create", "build", "write", "fix", "deploy", "implement", "reduce", "increase",
            "achieve", "run", "test", "verify", "organize", "refactor", "complete", "lose",
            "optimize", "design", "develop", "audit", "publish", "release"
        ]
        has_action = any(re.search(r"\b" + re.escape(v) + r"\b", text, re.I) for v in action_verbs)
        if has_action:
            score_s += 1.5
        if len(text.split()) >= 4 or len(text) >= 12:
            score_s += 1.0
        vague_words = ["stuff", "things", "better", "improve somehow", "do something", "whatever"]
        if any(w in text.lower() for w in vague_words):
            score_s -= 1.5
            s_suggestions.append("Clarify the exact deliverable instead of vague actions.")
        score_s = max(1.0, min(5.0, score_s))

        # 2. Measurable
        score_m = 1.5
        m_suggestions = []
        has_numbers = bool(re.search(cls.NUM_PATTERN, text))
        units = ["%", "percent", "kg", "lbs", "times", "steps", "tests", "files", "lines", "errors", "issues", "ms", "s"]
        has_units = any(u in text.lower() for u in units)
        if has_numbers and has_units:
            score_m = 5.0
        elif has_numbers:
            score_m = 4.0
        else:
            m_suggestions.append("Add a quantifiable metric (e.g. 5 kg, 10 tests, 100% coverage).")
        score_m = max(1.0, min(5.0, score_m))

        # 3. Achievable
        score_a = 4.0
        a_suggestions = []
        # Detect absurd or extreme goals
        extreme_patterns = [
            (r"lose\s*(\d+)\s*(?:kg|lbs)", lambda val: float(val) > 20, "Losing more than 20kg at once may be unhealthy; pace by 0.5-1kg/week."),
            (r"(?:100%|0 error)", lambda val: False, ""),
        ]
        for pattern, predicate, msg in extreme_patterns:
            m = re.search(pattern, text, re.I)
            if m:
                try:
                    if predicate(m.group(1)):
                        score_a -= 2.0
                        if msg:
                            a_suggestions.append(msg)
                except (IndexError, ValueError):
                    pass
        score_a = max(1.0, min(5.0, score_a))

        # 4. Relevant
        score_r = 4.5
        if len(text) < 5:
            score_r = 2.0

        # 5. Time-bound
        score_t = 1.5
        t_suggestions = []
        time_patterns = [
            rf"(?:in|within|by|for)\s+{cls.NUM_PATTERN}\s*{cls.TIME_UNITS}",
            r"(?:daily|weekly|monthly|every\s+day|today|this\s+week)",
            r"(?:before|by)\s+(?:\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|tomorrow|next\s+week)",
        ]
        if any(re.search(p, text, re.I) for p in time_patterns):
            score_t = 5.0
        else:
            t_suggestions.append("Specify a clear deadline or cadence (e.g. 'within 2 weeks' or 'daily for 30 days').")
        score_t = max(1.0, min(5.0, score_t))

        scores = {
            "specific": round(score_s, 1),
            "measurable": round(score_m, 1),
            "achievable": round(score_a, 1),
            "relevant": round(score_r, 1),
            "time_bound": round(score_t, 1),
        }

        overall = round(sum(scores.values()) / len(scores), 1)

        if overall >= 4.5:
            grade = "S"
            assessment = "Outstanding SMART goal with clear scope, quantitative metrics, and deadlines."
        elif overall >= 4.0:
            grade = "A"
            assessment = "Strong SMART goal. Minor adjustments to metrics or milestones could optimize execution."
        elif overall >= 3.0:
            grade = "B"
            assessment = "Moderate goal definition. Needs clearer metrics or explicit timeline to be fully actionable."
        else:
            grade = "C"
            assessment = "Underspecified goal. Requires measurable indicators and timeframe definition."

        suggestions = s_suggestions + m_suggestions + a_suggestions + t_suggestions
        if not suggestions:
            suggestions.append("Goal is well structured. Proceed with phased milestones.")

        milestones = cls._generate_milestones(text, scores)

        return SmartValidationResult(
            goal=objective,
            smart_scores=scores,
            overall_score=overall,
            grade=grade,
            assessment=assessment,
            suggestions=suggestions,
            milestones=milestones,
        )

    @classmethod
    def _generate_milestones(cls, objective: str, scores: Dict[str, float]) -> List[str]:
        """Generate phased milestone checklist."""
        milestones = [
            "Milestone 1: Baseline assessment and environment setup",
            "Milestone 2: Core execution and 50% milestone verification",
            "Milestone 3: Final validation, testing, and completion audit",
        ]
        return milestones

    @classmethod
    def track_progress(
        cls,
        current_value: float,
        target_value: float,
        start_value: float = 0.0,
        elapsed_days: float = 0.0,
        total_days: float = 0.0,
    ) -> ProgressResult:
        """
        Track progress towards a quantifiable numerical target.
        Handles both ascending (increasing) and descending (reduction) goals.
        """
        delta_total = target_value - start_value
        if delta_total == 0:
            comp_pct = 100.0 if current_value == target_value else 0.0
        else:
            comp_pct = ((current_value - start_value) / delta_total) * 100.0

        comp_pct = max(0.0, min(100.0, comp_pct))
        time_pct = (elapsed_days / total_days * 100.0) if total_days > 0 else 0.0

        velocity = ((current_value - start_value) / elapsed_days) if elapsed_days > 0 else 0.0

        if comp_pct >= 100.0:
            status = "ahead"
            assessment = "Goal completed! Target reached or exceeded."
            est_days = 0.0
            suggestions = ["Goal achieved. Archive or set next target."]
        elif total_days > 0:
            ratio = comp_pct / time_pct if time_pct > 0 else 1.0
            if ratio >= 1.15:
                status = "ahead"
                assessment = "Progress is ahead of schedule."
                suggestions = ["Keep up momentum."]
            elif ratio >= 0.85:
                status = "on_track"
                assessment = "Progress is on track according to schedule."
                suggestions = ["Maintain steady cadence."]
            elif ratio >= 0.5:
                status = "behind"
                assessment = "Progress is lagging slightly behind schedule."
                suggestions = ["Increase daily focus or break down pending blockers."]
            else:
                status = "critically_behind"
                assessment = "Progress is significantly behind schedule."
                suggestions = ["Reassess goal scope or reallocate resources immediately."]

            remaining_delta = target_value - current_value
            if velocity != 0 and (remaining_delta / velocity) > 0:
                est_days = round(remaining_delta / velocity, 1)
            else:
                est_days = None
        else:
            status = "on_track"
            assessment = f"Current progress: {comp_pct:.1f}%."
            est_days = None
            suggestions = ["Establish a target completion date."]

        return ProgressResult(
            current_value=current_value,
            target_value=target_value,
            start_value=start_value,
            completion_percentage=round(comp_pct, 1),
            time_percentage=round(time_pct, 1),
            velocity=round(velocity, 2),
            status=status,
            estimated_days_remaining=est_days,
            assessment=assessment,
            suggestions=suggestions,
        )

    @classmethod
    def analyze_habit(
        cls,
        habit_name: str,
        current_streak: int,
        longest_streak: int,
        total_days: int,
        completed_days: int,
    ) -> HabitResult:
        """
        Analyze habit formation based on streak and consistency metrics.
        Stages:
        - 1-7 days: Ignition
        - 8-21 days: Formation
        - 22-30 days: Consolidation
        - 31-66 days: Habit
        - 67+ days: Automated
        """
        rate = (completed_days / total_days * 100.0) if total_days > 0 else 0.0

        if current_streak >= 67:
            stage = "Automated"
            next_m = current_streak + 30
            score = 10.0
            assessment = "Habit is fully automated and deeply ingrained."
        elif current_streak >= 31:
            stage = "Habit"
            next_m = 67
            score = 8.5
            assessment = "Strong habit foundation established. Approaching automation."
        elif current_streak >= 22:
            stage = "Consolidation"
            next_m = 31
            score = 7.0
            assessment = "Consolidation phase. Maintain consistency to solidify neural pathways."
        elif current_streak >= 8:
            stage = "Formation"
            next_m = 22
            score = 5.0
            assessment = "Formation phase. Resistance is decreasing."
        else:
            stage = "Ignition"
            next_m = 8
            score = max(1.0, current_streak * 0.5)
            assessment = "Ignition phase. High friction; prioritize starting small."

        suggestions = []
        if rate < 70.0:
            suggestions.append("Consistency rate is low; try habit stacking with an existing daily anchor.")
        if current_streak == longest_streak and current_streak > 5:
            suggestions.append(f"New personal record streak of {current_streak} days! Keep the streak alive.")

        return HabitResult(
            habit_name=habit_name,
            current_streak=current_streak,
            longest_streak=longest_streak,
            total_days=total_days,
            completed_days=completed_days,
            completion_rate=round(rate, 1),
            strength_score=round(score, 1),
            stage=stage,
            next_milestone=next_m,
            assessment=assessment,
            suggestions=suggestions,
        )
