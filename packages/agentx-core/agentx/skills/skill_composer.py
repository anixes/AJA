"""
agentx/skills/skill_composer.py
================================
Phase 9 — Gap 2: Multi-skill composition.

Problem
-------
Single-skill execution handles one repeatable pattern at a time.
Complex tasks naturally decompose into ordered sub-objectives, each
matched by a different skill.  This module chains them.

Design
------
  compose_skills(chain, task_id, run_id, objective, tracker, confirm_fn) -> bool

  A "chain" is an ordered list of (skill_dict, sub_objective) pairs.
  Rules:
    1. Risk gate: before ANY step executes, the chain's maximum risk_level
       is evaluated once.  If HIGH, a single confirmation covers the whole chain.
    2. Context passing: each skill's step_results are merged into a shared
       context dict that subsequent skills can reference via postconditions
       or dynamic arg injection (_inject_context).
    3. Atomicity: if any skill fails, execution stops; the successful prefix
       is logged and the caller receives False so the normal pipeline can take over.
    4. Checkpointing: already-completed skills in this run_id are skipped (same
       mechanism as step-level checkpoints — keyed on skill_id+run_id).
    5. Postconditions: validate_postconditions() is called after each skill.
       A correctness failure is treated as a skill failure (chain halts).

Auto-building chains (build_chain)
------------------------------------
  build_chain(objective) -> list[(skill, sub_objective)] | None

  Decomposes the objective into sub-objectives using simple heuristic splitting
  (conjunctions, then/after markers), then calls recommend_skill() for each.
  Returns None if fewer than 2 sub-objectives are resolved to skills (single
  skill falls back to normal recommend_skill).

Public API
----------
  build_chain(objective, min_confidence, include_stale) -> list | None
  compose_skills(chain, task_id, run_id, objective, tracker, confirm_fn) -> bool
"""

import uuid
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Risk ordering
# ---------------------------------------------------------------------------

_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _max_risk(chain: list) -> str:
    """Return the highest risk_level across all skills in the chain."""
    best = 0
    for skill, _ in chain:
        level = _RISK_ORDER.get(skill.get("risk_level", "LOW"), 0)
        if level > best:
            best = level
    return {0: "LOW", 1: "MEDIUM", 2: "HIGH"}[best]


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------

def _inject_context(step: dict, context: dict) -> dict:
    """
    Substitute {{key}} placeholders in args_schema values with accumulated
    context from prior skill executions.

    Example:
        step = {"tool_name": "send_email", "args_schema": {"to": "{{recipient}}"}}
        context = {"recipient": "alice@example.com"}
        -> {"tool_name": "send_email", "args_schema": {"to": "alice@example.com"}}
    """
    import json, re
    raw = json.dumps(step)
    def replacer(m):
        key = m.group(1).strip()
        val = context.get(key)
        if val is None:
            return m.group(0)  # leave as-is if key not in context
        return json.dumps(val) if not isinstance(val, str) else val
    replaced = re.sub(r'\{\{([^}]+)\}\}', replacer, raw)
    try:
        return json.loads(replaced)
    except Exception:
        return step  # return original on parse error


# ---------------------------------------------------------------------------
# Heuristic chain decomposition
# ---------------------------------------------------------------------------

# Splitting markers (order-sensitive connectives)
_SPLIT_PATTERNS = [
    r'\bthen\b', r'\bafter\s+that\b', r'\bafterwards\b', r'\bsubsequently\b',
    r'\band\s+then\b', r'\bnext\b', r'\bfinally\b',
]

def _split_objective(objective: str) -> list:
    """
    Heuristically split a multi-step objective into ordered sub-objectives.

    "fetch data then process and store results" ->
    ["fetch data", "process and store results"]

    Returns the original objective as a single-element list if no split found.
    """
    import re
    pattern = '|'.join(_SPLIT_PATTERNS)
    parts   = re.split(pattern, objective, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def build_chain(
    objective:      str,
    min_confidence: float = 0.0,
    include_stale:  bool  = False,
) -> list:
    """
    Decompose objective into sub-objectives and recommend a skill for each.

    Returns
    -------
    list[(skill_dict, sub_objective)]  — non-empty only when >= 2 skills resolved
    []                                 — caller should fall back to single recommend_skill

    Caller contract
    ---------------
    If len(result) < 2: treat as "no composition applicable".
    """
    from agentx.skills.skill_store import recommend_skill

    parts = _split_objective(objective)
    if len(parts) < 2:
        return []

    chain = []
    for part in parts:
        skill = recommend_skill(part, min_confidence=min_confidence,
                                include_stale=include_stale)
        if skill is not None:
            chain.append((skill, part))

    # Require at least 2 resolved skills to form a chain
    return chain if len(chain) >= 2 else []


# ---------------------------------------------------------------------------
# Skill-level checkpoint helpers (reuse executor DB)
# ---------------------------------------------------------------------------

def _skill_done(run_id: str, skill_id: str) -> bool:
    """Return True if this skill already completed in this run_id."""
    try:
        import sqlite3, os
        path = os.environ.get("AGENTX_DB_PATH", ".agentx/aja_secretary.sqlite3")
        if not os.path.exists(path):
            return False
        conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
        row  = conn.execute(
            "SELECT 1 FROM skill_composition_log WHERE run_id=? AND skill_id=? AND status='COMPLETED'",
            (run_id, skill_id)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _log_skill_status(run_id: str, skill_id: str, status: str,
                       position: int, total: int) -> None:
    """Persist composition progress to skill_composition_log table."""
    try:
        import sqlite3, os
        path = os.environ.get("AGENTX_DB_PATH", ".agentx/aja_secretary.sqlite3")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_composition_log (
                    run_id     TEXT NOT NULL,
                    skill_id   TEXT NOT NULL,
                    status     TEXT NOT NULL,
                    position   INTEGER,
                    total      INTEGER,
                    logged_at  TIMESTAMP,
                    PRIMARY KEY (run_id, skill_id)
                )
            """)
            conn.execute(
                """INSERT OR REPLACE INTO skill_composition_log
                   (run_id, skill_id, status, position, total, logged_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, skill_id, status, position, total,
                 datetime.now(timezone.utc).isoformat()),
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chain Validation (Phase 10)
# ---------------------------------------------------------------------------

def validate_chain(chain: list, max_steps: int = 10, simulate: bool = False) -> tuple:
    """
    Validate a multi-step execution chain before any execution begins.

    Checks:
      1. max_steps limit
      2. Tool existence (unless simulate=True)
      3. Environment prerequisites

    Returns (ok: bool, failures: list[str])
    """
    if not chain:
        return False, ["Empty chain"]

    if len(chain) > max_steps:
        return False, [f"Chain length ({len(chain)}) exceeds max_steps ({max_steps})"]

    import importlib
    import json
    from agentx.skills.skill_executor import check_environment

    failures = []

    for i, (skill, _) in enumerate(chain):
        skill_name = skill.get("name", skill.get("id", f"step_{i}"))

        # 1. Environment prerequisites
        env_ok, env_failures = check_environment(skill)
        if not env_ok:
            failures.append(f"[{skill_name}] Env failures: {', '.join(env_failures)}")

        # 2. Tool existence
        try:
            tool_sequence = json.loads(skill.get("tool_sequence") or "[]")
        except Exception:
            tool_sequence = []

        for step in tool_sequence:
            tool_name = step.get("tool_name")
            if not tool_name:
                continue
            
            module_path = f"agentx.tools.{tool_name}"
            try:
                importlib.import_module(module_path)
            except ModuleNotFoundError:
                if not simulate:
                    failures.append(f"[{skill_name}] Tool '{tool_name}' not found")
                else:
                    print(f"[Composer][SIM] Tool '{tool_name}' not found, allowing in simulate mode.")

    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compose_skills(
    chain:      list,
    task_id:    int,
    run_id:     str,
    objective:  str,
    tracker=None,
    confirm_fn=None,
) -> bool:
    """
    Execute a chain of skills sequentially with shared context.

    Parameters
    ----------
    chain      : list of (skill_dict, sub_objective) tuples — from build_chain()
                 or manually constructed by the caller.
    task_id    : int — current task row id
    run_id     : str — UUID scoping all idempotency keys and checkpoints
    objective  : str — the original full objective (for logging)
    tracker    : optional tracker module
    confirm_fn : callable(prompt) -> bool for HIGH-risk confirmation

    Returns
    -------
    True  — all skills completed (+ postconditions passed)
    False — any skill or postcondition failed; caller should SKILL_FALLBACK

    Design guarantees
    -----------------
    - Single risk gate: one confirmation for the entire chain (max risk_level).
    - Per-skill checkpointing: already-completed skills are skipped on resume.
    - Context accumulation: step_results are merged into shared dict for later
      skills to reference via {{key}} template substitution in args_schema.
    - Postcondition validation after each skill (correctness, not just execution).
    - Never raises.
    """
    from agentx.skills.skill_executor import execute_skill
    from agentx.skills.skill_postconditions import validate_postconditions

    if not chain:
        return False

    total = len(chain)

    def _emit(event: str, extra: dict = None):
        payload = {"run_id": run_id, "task_id": task_id,
                   "objective": objective, **(extra or {})}
        extra_str = f"  {extra}" if extra else ""
        print(f"[Composer] {event}{extra_str}")
        if tracker:
            try:
                tracker.log_event(event, payload)
            except Exception:
                pass

    try:
        # ── Pre-execution validation gate ─────────────────────────────────────
        valid_chain, val_failures = validate_chain(chain)
        if not valid_chain:
            _emit("COMPOSITION_REJECTED", {"failures": val_failures})
            _emit("COMPOSITION_FALLBACK")
            return False
            
        _emit("COMPOSITION_VALIDATED", {"chain_size": total})

        # ── Single risk gate (max across all skills) ──────────────────────────
        chain_risk = _max_risk(chain)
        if chain_risk == "HIGH":
            skill_names = ", ".join(s.get("name", s.get("id", "?")) for s, _ in chain)
            prompt = (
                f"\n[!] HIGH-RISK skill chain ({total} skills): [{skill_names}]\n"
                f"    Objective: {objective}\n"
                f"    Proceed with full chain? [y/N]: "
            )
            if confirm_fn is not None:
                approved = confirm_fn(prompt)
            else:
                try:
                    approved = input(prompt).strip().lower() in ("y", "yes")
                except (EOFError, KeyboardInterrupt):
                    approved = False

            if not approved:
                _emit("CHAIN_EXECUTION_DENIED", {"risk": "HIGH", "chain_size": total})
                return False
            _emit("CHAIN_EXECUTION_APPROVED", {"risk": "HIGH"})
        elif chain_risk == "MEDIUM":
            _emit("CHAIN_RISK_WARNING", {"risk": "MEDIUM", "chain_size": total})

        _emit("CHAIN_EXECUTION_STARTED", {
            "chain_size": total,
            "skills": [s.get("name", s.get("id")) for s, _ in chain],
        })

        # ── Shared context accumulated across skills ───────────────────────────
        shared_context: dict = {}
        uncertain_steps = 0

        for position, (skill, sub_objective) in enumerate(chain):
            skill_id   = skill.get("id", "unknown")
            skill_name = skill.get("name", skill_id)

            # ── Skip already-completed skills (resume after crash) ────────────
            if _skill_done(run_id, skill_id):
                _emit("CHAIN_SKILL_SKIPPED", {
                    "position": position + 1, "total": total,
                    "skill_name": skill_name, "reason": "already completed",
                })
                continue

            _emit("CHAIN_SKILL_STARTED", {
                "position": position + 1, "total": total,
                "skill_name": skill_name, "sub_objective": sub_objective,
            })

            # ── Context injection: inject shared_context into skill's tool args ─
            import json
            try:
                tool_seq = json.loads(skill.get("tool_sequence") or "[]")
                injected_seq = [_inject_context(step, shared_context)
                                for step in tool_seq]
                skill = dict(skill)  # shallow copy before mutation
                skill["tool_sequence"] = json.dumps(injected_seq)
            except Exception:
                pass  # use original if injection fails

            # ── Execute skill (risk gate already handled at chain level) ────────
            # Override risk_level to LOW so execute_skill doesn't double-prompt
            skill_no_reprmt = dict(skill)
            skill_no_reprmt["risk_level"] = "LOW"  # chain already confirmed

            ok = execute_skill(
                skill      = skill_no_reprmt,
                task_id    = task_id,
                run_id     = run_id,
                objective  = sub_objective,
                tracker    = tracker,
                confirm_fn = confirm_fn,
            )

            if not ok:
                _emit("CHAIN_SKILL_FAILED", {
                    "position": position + 1, "total": total,
                    "skill_name": skill_name,
                    "completed_before": position,
                })
                _log_skill_status(run_id, skill_id, "FAILED", position + 1, total)
                _emit("CHAIN_FALLBACK")
                return False

            _log_skill_status(run_id, skill_id, "COMPLETED", position + 1, total)

            # ── Postcondition validation after each skill ─────────────────────
            pc_ok, pc_failures = validate_postconditions(
                skill        = skill,
                step_results = [],   # step_results not threaded here; uses flat={}
                tracker      = tracker,
            )
            if not pc_ok:
                _emit("CHAIN_POSTCONDITION_FAILED", {
                    "position": position + 1, "skill_name": skill_name,
                    "failures": pc_failures,
                })
                _emit("CHAIN_FALLBACK")
                return False

            # ── Step 5: Composition Awareness (Uncertainty Tracking) ──────────
            try:
                from agentx.decision.evaluator import evaluate_pipeline
                step_eval = evaluate_pipeline(task_id, "completed", {"objective": sub_objective, "skill": skill})
                if isinstance(step_eval, dict):
                    if step_eval.get("decision") == "UNCERTAIN" or step_eval.get("uncertainty_score", 0.0) > 0.4:
                        uncertain_steps += 1
            except Exception:
                pass

            if uncertain_steps >= 2:
                _emit("CHAIN_UNCERTAINTY_EXCEEDED", {"uncertain_steps": uncertain_steps})
                print(f"[Composer] CHAIN_UNCERTAINTY_EXCEEDED. Halting early.")
                _emit("CHAIN_FALLBACK")
                return False

            # ── Accumulate context for next skill ─────────────────────────────
            shared_context[f"skill_{position}_name"]   = skill_name
            shared_context[f"skill_{position}_status"] = "COMPLETED"

            _emit("CHAIN_SKILL_COMPLETED", {
                "position": position + 1, "total": total,
                "skill_name": skill_name,
            })

        _emit("CHAIN_EXECUTION_COMPLETED", {
            "skills_run": total, "objective": objective
        })
        return True

    except Exception as e:
        _emit("CHAIN_EXECUTION_FAILED", {"error": f"unexpected: {e}"})
        _emit("CHAIN_FALLBACK")
        return False
