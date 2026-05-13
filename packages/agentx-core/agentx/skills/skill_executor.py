"""
agentx/skills/skill_executor.py
================================
Phase 8B + 8B.1 (Final gaps 1–3) — Safe skill execution within orchestration guarantees.

Design constraints (NEVER violate):
  - Every tool step goes through ToolGuard (idempotency, caching, failure classification).
  - No tool is called directly — only via ToolGuard.reserve() / complete() / fail().
  - Risk gate enforced BEFORE execution; HIGH-risk requires explicit confirmation.
  - Failure in any step triggers SKILL_FALLBACK; the normal pipeline then takes over.
  - Metrics (success_count, failure_count, confidence_score) updated atomically after result.
  - This module never raises — all exceptions are caught and logged.

Gap 1 — Step-level recovery:
  Completed steps are checkpointed in `skill_step_checkpoints`.
  On re-execution with the same (skill_id, run_id), already-completed steps are skipped.

Gap 2 — Environment validation:
  Prerequisites declared by the skill are checked against a pluggable
  _ENV_VALIDATORS registry.  Unmet prerequisites abort before any tool is called.

Gap 3 — Validity decay:
  `last_used_at` is updated on every attempt.  `mark_stale_skills()` is called
  on startup (via agent.py main()) to retire skills older than STALE_AFTER_DAYS.
  Stale skills are excluded from recommend_skill().

Public API
----------
  execute_skill(skill, task_id, run_id, objective, tracker, confirm_fn) -> bool
  mark_stale_skills(stale_after_days)   → int  (skills marked stale)
  check_environment(skill)              → (ok: bool, failures: list[str])
"""

import json
import os
import pyarrow as pa
from datetime import datetime, timezone, timedelta
from agentx.memory.manager import (
    MemoryManager,
    get_memory_manager,
    list_tables_defensive,
)

_manager = get_memory_manager()

# Arrow schema for step-level checkpoints
_CHECKPOINT_SCHEMA = pa.schema(
    [
        ("checkpoint_id", pa.string()),
        ("skill_id", pa.string()),
        ("run_id", pa.string()),
        ("step_index", pa.int32()),
        ("tool_name", pa.string()),
        ("result", pa.string()),
        ("completed_at", pa.string()),
    ]
)


def _ensure_checkpoint_table():
    existing = list_tables_defensive(_manager.db)
    if "skill_step_checkpoints" not in existing:
        _manager.db.create_table("skill_step_checkpoints", schema=_CHECKPOINT_SCHEMA)


_ensure_checkpoint_table()


# ---------------------------------------------------------------------------
# Gap 3 — Validity decay
# ---------------------------------------------------------------------------

STALE_AFTER_DAYS = 30


def mark_stale_skills(stale_after_days: int = STALE_AFTER_DAYS) -> int:
    """
    Mark skills unused for stale_after_days days as stale in the Arrow skill store.
    Returns the count of skills newly marked stale.
    """
    from agentx.skills.skill_store import mark_skills_stale

    try:
        return mark_skills_stale(stale_after_days)
    except Exception as e:
        print(f"[SkillExec] mark_stale_skills() error: {e}")
        return 0


def _refresh_last_used(skill_id: str) -> None:
    """Touch last_used_at + clear stale flag whenever a skill is attempted."""
    try:
        from agentx.skills.skill_store import touch_skill

        touch_skill(skill_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Gap 2 — Environment validation
# ---------------------------------------------------------------------------

# Registry: prerequisite string (lowercase, stripped) → validator callable.
# Each validator returns (ok: bool, detail: str).
# Add entries here to grow coverage without touching execute_skill().
_ENV_VALIDATORS: dict = {
    "network connectivity": lambda: _check_network(),
    "database connection available": lambda: _check_db_available(),
    "email credentials configured": lambda: _check_env_vars("SMTP_HOST", "SMTP_USER"),
    "storage access granted": lambda: _check_env_vars("STORAGE_PATH"),
    "authentication tokens valid": lambda: _check_env_vars("AUTH_TOKEN", "API_KEY"),
}


def _check_network() -> tuple:
    import socket

    try:
        socket.setdefaulttimeout(2)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True, ""
    except Exception as e:
        return False, f"No network: {e}"


def _check_db_available() -> tuple:
    """Consider DB available if the Agent DB file exists and is readable."""
    path = _db_path()
    if os.path.exists(path):
        return True, ""
    return False, f"DB file not found: {path}"


def _check_env_vars(*names: str) -> tuple:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        return False, f"Missing env vars: {', '.join(missing)}"
    return True, ""


def check_environment(skill: dict) -> tuple:
    """
    Validate skill prerequisites against the current runtime environment.

    Returns (ok: bool, failures: list[str]).
      ok=True  → all prerequisites satisfied; safe to execute.
      ok=False → list of unmet prerequisite descriptions.

    Unknown prerequisites are logged as warnings (not failures) so that
    custom prerequisites added by the LLM don't hard-block execution.
    """
    try:
        raw = skill.get("prerequisites") or "[]"
        prereqs: list = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        prereqs = []

    if not prereqs or prereqs == ["no specific prerequisites identified"]:
        return True, []

    failures = []
    for prereq in prereqs:
        key = prereq.strip().lower()
        validator = _ENV_VALIDATORS.get(key)
        if validator is None:
            # Unknown prerequisite — warn but don't block
            print(f"[SkillExec][ENV] Unknown prerequisite (skipped): '{prereq}'")
            continue
        ok, detail = validator()
        if not ok:
            failures.append(f"{prereq}: {detail}" if detail else prereq)

    return (len(failures) == 0), failures


# ---------------------------------------------------------------------------
# Risk gate (Step 2)
# ---------------------------------------------------------------------------


def _risk_gate(skill: dict, confirm_fn=None) -> bool:
    """
    Enforce execution gate based on skill risk_level.

    HIGH   → requires explicit confirmation via confirm_fn (or CLI prompt).
             Returns False if denied.
    MEDIUM → logs a warning, proceeds.
    LOW    → proceeds silently.

    confirm_fn: callable(prompt: str) -> bool
        Inject a custom confirmer (Telegram, tests, etc.).
        Defaults to CLI input() when None.
    """
    risk = skill.get("risk_level", "LOW")
    name = skill.get("name", skill.get("id", "?"))

    if risk == "HIGH":
        prompt = (
            f"\n[!] HIGH-RISK skill selected: '{name}'\n"
            f"    Pitfalls : {skill.get('pitfalls', 'N/A')}\n"
            f"    Proceed? [y/N]: "
        )
        if confirm_fn is not None:
            approved = confirm_fn(prompt)
        else:
            try:
                approved = input(prompt).strip().lower() in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                approved = False

        if not approved:
            print(f"[SkillExec] HIGH-risk execution DENIED by operator: '{name}'")
            return False
        print(f"[SkillExec] HIGH-risk execution APPROVED: '{name}'")

    elif risk == "MEDIUM":
        print(
            f"[SkillExec][WARN] MEDIUM-risk skill: '{name}' — "
            f"{skill.get('pitfalls', 'review before use')}"
        )

    return True


# ---------------------------------------------------------------------------
# Gap 1 — Step-level checkpoint helpers
# ---------------------------------------------------------------------------


def _load_completed_steps(skill_id: str, run_id: str) -> dict:
    """Return {step_index: result} for all already-completed steps."""
    try:
        t = _manager.db.open_table("skill_step_checkpoints")
        rows = (
            t.search()
            .where(f"skill_id = '{skill_id}' AND run_id = '{run_id}'")
            .to_list()
        )
        return {r["step_index"]: r["result"] for r in rows}
    except Exception:
        return {}


def _checkpoint_step(
    skill_id: str, run_id: str, step_index: int, tool_name: str, result: str
) -> None:
    """Persist a completed step checkpoint (upsert for idempotency)."""
    import uuid

    try:
        t = _manager.db.open_table("skill_step_checkpoints")
        existing = (
            t.search()
            .where(
                f"skill_id = '{skill_id}' AND run_id = '{run_id}' AND step_index = {step_index}"
            )
            .limit(1)
            .to_list()
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        if existing:
            t.update(
                where=f"skill_id = '{skill_id}' AND run_id = '{run_id}' AND step_index = {step_index}",
                values={"result": result, "completed_at": now_iso},
            )
        else:
            t.add(
                [
                    {
                        "checkpoint_id": uuid.uuid4().hex,
                        "skill_id": skill_id,
                        "run_id": run_id,
                        "step_index": step_index,
                        "tool_name": tool_name,
                        "result": result,
                        "completed_at": now_iso,
                    }
                ]
            )
    except Exception as e:
        print(f"[SkillExec] _checkpoint_step() error: {e}")


def _clear_checkpoints(skill_id: str, run_id: str) -> None:
    """Mark checkpoints complete after full success — LanceDB has no row-delete in OSS."""
    try:
        t = _manager.db.open_table("skill_step_checkpoints")
        t.update(
            where=f"skill_id = '{skill_id}' AND run_id = '{run_id}'",
            values={"result": "__CLEARED__"},
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tool step executor (Step 3) — all calls go through ToolGuard
# ---------------------------------------------------------------------------


def _execute_step(run_id: str, step: dict, step_index: int) -> tuple:
    """
    Execute a single tool_sequence step via ToolGuard.

    Returns (success: bool, result: str | None, error: str | None).

    The actual tool implementation is looked up via the tool registry.
    If no real implementation exists, the step is recorded as a
    SIMULATED execution (safe for replay/testing).
    """
    from agentx.persistence.tools import ToolGuard

    tool_name = step.get("tool_name", "unknown")
    args = step.get("args_schema", {})

    guard = ToolGuard(
        run_id=run_id,
        tool_name=tool_name,
        args=args,
        step=f"skill_step_{step_index}",
    )

    cached = guard.reserve()

    if cached is not None:
        # Coalesce: already COMPLETED or currently RUNNING by another execution
        status = cached.get("status", "COMPLETED")
        if status == "COMPLETED" or "result" in cached:
            print(
                f"[SkillExec][Step {step_index}] Coalesced cached result for '{tool_name}'"
            )
            return True, cached.get("result"), None
        # Another runner holds the reservation — treat as transient failure
        return False, None, f"Tool '{tool_name}' already RUNNING (concurrent execution)"

    # Attempt to call the real tool implementation via registry
    result, error = _invoke_tool(tool_name, args)

    if error is None:
        guard.complete(result or "ok")
        return True, result, None
    else:
        # Classify: permanent errors should not be retried
        error_type = "PERMANENT" if _is_permanent_error(error) else "RETRYABLE"
        guard.fail(error, error_type=error_type)
        return False, None, error


def _invoke_tool(tool_name: str, args: dict) -> tuple:
    """
    Look up and call a real tool implementation.

    Tool implementations live in agentx/tools/<tool_name>.py and expose
    a run(args: dict) -> str function.  If no implementation exists,
    the step is simulated (logged but not executed for real).

    Returns (result: str | None, error: str | None).
    """
    import importlib

    module_path = f"agentx.tools.{tool_name}"
    try:
        mod = importlib.import_module(module_path)
        result = mod.run(args)
        return str(result), None
    except ModuleNotFoundError:
        # No real implementation — simulate (safe default)
        simulated = json.dumps(
            {"simulated": True, "tool": tool_name, "args_keys": list(args.keys())}
        )
        print(f"[SkillExec][SIM] No impl for '{tool_name}' — simulating step.")
        return simulated, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _is_permanent_error(error: str) -> bool:
    """Classify whether an error should never be retried."""
    permanent_signals = (
        "AuthenticationError",
        "PermissionError",
        "InvalidInput",
        "NotFound",
        "400",
        "401",
        "403",
        "404",
        "422",
    )
    return any(sig in error for sig in permanent_signals)


# ---------------------------------------------------------------------------
# Metrics update (Step 5)
# ---------------------------------------------------------------------------


def _update_skill_metrics(skill_id: str, success: bool) -> None:
    """Atomically update success_count / failure_count / confidence_score via skill_store."""
    try:
        from agentx.skills.skill_store import update_skill_metrics

        update_skill_metrics(skill_id, success=success)
    except Exception as e:
        print(f"[SkillExec] _update_skill_metrics() error: {e}")


# ---------------------------------------------------------------------------
# Main entry point (Steps 1 – 6 + Gaps 1, 2, 3)
# ---------------------------------------------------------------------------


def execute_skill(
    skill: dict,
    task_id: int,
    run_id: str,
    objective: str,
    tracker=None,
    confirm_fn=None,
) -> bool:
    """
    Execute a recommended skill safely within system guarantees.

    Parameters
    ----------
    skill      : dict from recommend_skill() — must include id, tool_sequence, risk_level
    task_id    : int  — current task row id (for logging / metrics)
    run_id     : str  — UUID from cmd_run (scopes ToolGuard idempotency keys + checkpoints)
    objective  : str  — original user objective (for log context)
    tracker    : agentx.persistence.tracker module (optional, for structured events)
    confirm_fn : callable(prompt) -> bool (optional; used for HIGH-risk gate in tests/Telegram)

    Returns
    -------
    True  — all steps completed; normal pipeline can be skipped or run in parallel.
    False — partial / full failure; caller should log SKILL_FALLBACK and continue normally.

    Gaps implemented
    ----------------
    Gap 1 — Step-level recovery: steps already completed in a previous attempt
             (same skill_id + run_id) are skipped rather than re-executed.
    Gap 2 — Environment validation: prerequisites checked before any tool call.
             Unmet prerequisites abort execution; unknown prerequisites warn only.
    Gap 3 — Validity decay: last_used_at is refreshed on every attempt;
             stale flag is cleared on reuse.
    """

    skill_id = skill.get("id", "unknown")
    skill_name = skill.get("name", skill_id)

    def _log(event: str, extra: dict = None):
        payload = {
            "skill_id": skill_id,
            "skill_name": skill_name,
            "task_id": task_id,
            "objective": objective,
            **(extra or {}),
        }
        print(f"[SkillExec] {event}  skill='{skill_name}'  task={task_id}")
        if tracker:
            try:
                tracker.log_event(event, payload)
            except Exception:
                pass

    try:
        # ── Gap 3: touch last_used_at (resets stale flag) ────────────────────
        _refresh_last_used(skill_id)

        # ── Step 6a — SKILL_SELECTED ─────────────────────────────────────────
        _log(
            "SKILL_SELECTED",
            {
                "risk_level": skill.get("risk_level", "LOW"),
                "confidence": skill.get("confidence_score", 0),
            },
        )

        # ── Step 2 — Risk gate (may prompt operator) ──────────────────────────
        if not _risk_gate(skill, confirm_fn=confirm_fn):
            _log("SKILL_EXECUTION_DENIED", {"reason": "operator denied HIGH-risk"})
            return False

        # ── Gap 2 — Environment validation ────────────────────────────────────
        env_ok, env_failures = check_environment(skill)
        if not env_ok:
            _log(
                "SKILL_EXECUTION_FAILED",
                {
                    "reason": "environment validation failed",
                    "failures": env_failures,
                },
            )
            _log("SKILL_FALLBACK")
            _update_skill_metrics(skill_id, success=False)
            return False

        # ── Step 6b — SKILL_EXECUTION_STARTED ───────────────────────────────
        _log("SKILL_EXECUTION_STARTED")

        # ── Step 3 — Parse tool_sequence ──────────────────────────────────────
        try:
            tool_sequence = json.loads(skill.get("tool_sequence") or "[]")
        except (json.JSONDecodeError, TypeError):
            tool_sequence = []

        if not tool_sequence:
            _log("SKILL_EXECUTION_FAILED", {"reason": "empty tool_sequence"})
            _log("SKILL_FALLBACK")
            _update_skill_metrics(skill_id, success=False)
            return False

        # ── Gap 1 — Load completed-step checkpoints ───────────────────────────
        done_steps = _load_completed_steps(skill_id, run_id)
        resumed_from = min(done_steps.keys()) if done_steps else None
        if done_steps:
            _log(
                "SKILL_RESUMING",
                {
                    "steps_already_done": sorted(done_steps.keys()),
                    "resuming_from_step": max(done_steps.keys()) + 1,
                },
            )

        # ── Step 3 — Execute each tool step via ToolGuard ────────────────────
        step_results = []
        for i, step in enumerate(tool_sequence):
            # Gap 1: skip steps already completed in a previous execution
            if i in done_steps:
                print(
                    f"[SkillExec][Step {i}] Skipping '{step.get('tool_name')}' "
                    f"(checkpoint found from prior run)"
                )
                step_results.append(
                    {
                        "step": i,
                        "tool": step.get("tool_name"),
                        "ok": True,
                        "recovered": True,
                    }
                )
                continue

            ok, result, error = _execute_step(run_id, step, step_index=i)
            step_results.append({"step": i, "tool": step.get("tool_name"), "ok": ok})

            if ok:
                # Gap 1: persist checkpoint so a crash here doesn't redo this step
                _checkpoint_step(
                    skill_id, run_id, i, step.get("tool_name", ""), result or "ok"
                )
            else:
                # Step 4 — Failure fallback
                _log(
                    "SKILL_EXECUTION_FAILED",
                    {
                        "failed_step": i,
                        "tool_name": step.get("tool_name"),
                        "error": error,
                        "steps_done": [r["step"] for r in step_results if r["ok"]],
                        "resume_hint": f"Re-run with same run_id='{run_id}' to resume from step {i}",
                    },
                )
                _log("SKILL_FALLBACK")
                # Step 5 — Failure metrics
                _update_skill_metrics(skill_id, success=False)
                # NOTE: checkpoints from steps 0..i-1 are intentionally kept
                # so the next execution of the same run_id resumes from step i.
                return False

        # ── Step 6c — SKILL_EXECUTION_COMPLETED ──────────────────────────────
        _log(
            "SKILL_EXECUTION_COMPLETED",
            {
                "steps_completed": len(step_results),
                "resumed_from": resumed_from,
            },
        )
        # Gap 1: cleanup checkpoints on full success
        _clear_checkpoints(skill_id, run_id)
        # Step 5 — Success metrics
        _update_skill_metrics(skill_id, success=True)
        return True

    except Exception as e:
        # Never raise — log and fallback
        _log("SKILL_EXECUTION_FAILED", {"error": f"unexpected: {e}"})
        _log("SKILL_FALLBACK")
        try:
            _update_skill_metrics(skill_id, success=False)
        except Exception:
            pass
        return False
