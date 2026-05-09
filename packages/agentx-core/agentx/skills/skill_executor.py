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
  on startup (via agentx.py main()) to retire skills older than STALE_AFTER_DAYS.
  Stale skills are excluded from recommend_skill().

Public API
----------
  execute_skill(skill, task_id, run_id, objective, tracker, confirm_fn) -> bool
  mark_stale_skills(stale_after_days)   → int  (skills marked stale)
  check_environment(skill)              → (ok: bool, failures: list[str])
"""

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# DB path — respects AGENTX_DB_PATH so tests can use an isolated sandbox
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return os.environ.get(
        "AGENTX_DB_PATH",
        os.path.join(".agentx", "aja_secretary.sqlite3"),
    )


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    _bootstrap_executor_tables(conn)
    return conn


def _bootstrap_executor_tables(conn: sqlite3.Connection) -> None:
    """Create tables needed exclusively by skill_executor (idempotent)."""
    # ── Gap 1: step-level checkpoint table ───────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_step_checkpoints (
            skill_id    TEXT NOT NULL,
            run_id      TEXT NOT NULL,
            step_index  INTEGER NOT NULL,
            tool_name   TEXT NOT NULL,
            result      TEXT,
            completed_at TIMESTAMP NOT NULL,
            PRIMARY KEY (skill_id, run_id, step_index)
        )
    """)
    # ── Gap 2+3: extend skills table with staleness tracking ─────────────
    # last_used_at — updated every time skill is attempted
    # is_stale      — set to 1 when validity decay kicks in
    for col_def in (
        "ALTER TABLE skills ADD COLUMN last_used_at  TIMESTAMP",
        "ALTER TABLE skills ADD COLUMN is_stale      INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(col_def)
        except Exception:
            pass  # column already exists
    conn.commit()


# ---------------------------------------------------------------------------
# Gap 3 — Validity decay
# ---------------------------------------------------------------------------

STALE_AFTER_DAYS = 30   # skills unused for this long are considered stale


def mark_stale_skills(stale_after_days: int = STALE_AFTER_DAYS) -> int:
    """
    Mark skills whose last_used_at (or created_at) is older than stale_after_days.

    Called once on AgentX startup.  Stale skills are excluded from
    recommend_skill() but NOT deleted — they can be reactivated by re-execution.

    Returns the number of skills newly marked stale.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=stale_after_days)
    ).isoformat()
    try:
        conn = _get_conn()
        cur = conn.execute(
            """UPDATE skills
               SET    is_stale = 1,
                      updated_at = ?
               WHERE  is_stale = 0
               AND    COALESCE(last_used_at, created_at) < ?""",
            (datetime.now(timezone.utc).isoformat(), cutoff),
        )
        marked = cur.rowcount
        conn.commit()
        conn.close()
        if marked:
            print(f"[SkillExec] Marked {marked} skill(s) stale (unused > {stale_after_days}d).")
        return marked
    except Exception as e:
        print(f"[SkillExec] mark_stale_skills() error: {e}")
        return 0


def _refresh_last_used(skill_id: str) -> None:
    """Touch last_used_at + clear stale flag whenever a skill is attempted."""
    try:
        conn = _get_conn()
        conn.execute(
            """UPDATE skills
               SET last_used_at = ?,
                   is_stale     = 0,
                   updated_at   = ?
               WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat(),
             skill_id),
        )
        conn.commit()
        conn.close()
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
    """Consider DB available if the AgentX DB file exists and is readable."""
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
    """
    Return {step_index: result} for all already-completed steps
    of this (skill_id, run_id) pair.
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT step_index, result FROM skill_step_checkpoints
               WHERE skill_id = ? AND run_id = ?""",
            (skill_id, run_id),
        ).fetchall()
        conn.close()
        return {r["step_index"]: r["result"] for r in rows}
    except Exception:
        return {}


def _checkpoint_step(skill_id: str, run_id: str, step_index: int,
                     tool_name: str, result: str) -> None:
    """Persist a completed step checkpoint (INSERT OR REPLACE for idempotency)."""
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO skill_step_checkpoints
               (skill_id, run_id, step_index, tool_name, result, completed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (skill_id, run_id, step_index, tool_name, result,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SkillExec] _checkpoint_step() error: {e}")


def _clear_checkpoints(skill_id: str, run_id: str) -> None:
    """Remove checkpoints after full success or terminal failure."""
    try:
        conn = _get_conn()
        conn.execute(
            "DELETE FROM skill_step_checkpoints WHERE skill_id = ? AND run_id = ?",
            (skill_id, run_id),
        )
        conn.commit()
        conn.close()
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
    args      = step.get("args_schema", {})

    guard = ToolGuard(
        run_id    = run_id,
        tool_name = tool_name,
        args      = args,
        step      = f"skill_step_{step_index}",
    )

    cached = guard.reserve()

    if cached is not None:
        # Coalesce: already COMPLETED or currently RUNNING by another execution
        status = cached.get("status", "COMPLETED")
        if status == "COMPLETED" or "result" in cached:
            print(f"[SkillExec][Step {step_index}] Coalesced cached result for '{tool_name}'")
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
        mod    = importlib.import_module(module_path)
        result = mod.run(args)
        return str(result), None
    except ModuleNotFoundError:
        # No real implementation — simulate (safe default)
        simulated = json.dumps({"simulated": True, "tool": tool_name,
                                "args_keys": list(args.keys())})
        print(f"[SkillExec][SIM] No impl for '{tool_name}' — simulating step.")
        return simulated, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _is_permanent_error(error: str) -> bool:
    """Classify whether an error should never be retried."""
    permanent_signals = (
        "AuthenticationError", "PermissionError", "InvalidInput",
        "NotFound", "400", "401", "403", "404", "422",
    )
    return any(sig in error for sig in permanent_signals)


# ---------------------------------------------------------------------------
# Metrics update (Step 5)
# ---------------------------------------------------------------------------

def _update_skill_metrics(skill_id: str, success: bool) -> None:
    """Atomically update success_count / failure_count / confidence_score."""
    try:
        path = _db_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT success_count, failure_count FROM skills WHERE id = ?",
                (skill_id,)
            ).fetchone()
            if row is None:
                return

            s    = row["success_count"] + (1 if success else 0)
            f    = row["failure_count"] + (0 if success else 1)
            conf = round(s / (s + f), 4) if (s + f) > 0 else 1.0

            conn.execute(
                """UPDATE skills
                   SET success_count    = ?,
                       failure_count    = ?,
                       confidence_score = ?,
                       updated_at       = ?
                   WHERE id = ?""",
                (s, f, conf, datetime.now(timezone.utc).isoformat(), skill_id),
            )
    except Exception as e:
        print(f"[SkillExec] _update_skill_metrics() error: {e}")


# ---------------------------------------------------------------------------
# Main entry point (Steps 1 – 6 + Gaps 1, 2, 3)
# ---------------------------------------------------------------------------

def execute_skill(
    skill:      dict,
    task_id:    int,
    run_id:     str,
    objective:  str,
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

    skill_id   = skill.get("id", "unknown")
    skill_name = skill.get("name", skill_id)

    def _log(event: str, extra: dict = None):
        payload = {"skill_id": skill_id, "skill_name": skill_name,
                   "task_id": task_id, "objective": objective, **(extra or {})}
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
        _log("SKILL_SELECTED", {"risk_level": skill.get("risk_level", "LOW"),
                                 "confidence": skill.get("confidence_score", 0)})

        # ── Step 2 — Risk gate (may prompt operator) ──────────────────────────
        if not _risk_gate(skill, confirm_fn=confirm_fn):
            _log("SKILL_EXECUTION_DENIED", {"reason": "operator denied HIGH-risk"})
            return False

        # ── Gap 2 — Environment validation ────────────────────────────────────
        env_ok, env_failures = check_environment(skill)
        if not env_ok:
            _log("SKILL_EXECUTION_FAILED", {
                "reason":   "environment validation failed",
                "failures": env_failures,
            })
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
            _log("SKILL_RESUMING", {
                "steps_already_done": sorted(done_steps.keys()),
                "resuming_from_step": max(done_steps.keys()) + 1,
            })

        # ── Step 3 — Execute each tool step via ToolGuard ────────────────────
        step_results = []
        for i, step in enumerate(tool_sequence):

            # Gap 1: skip steps already completed in a previous execution
            if i in done_steps:
                print(f"[SkillExec][Step {i}] Skipping '{step.get('tool_name')}' "
                      f"(checkpoint found from prior run)")
                step_results.append({"step": i, "tool": step.get("tool_name"),
                                     "ok": True, "recovered": True})
                continue

            ok, result, error = _execute_step(run_id, step, step_index=i)
            step_results.append({"step": i, "tool": step.get("tool_name"), "ok": ok})

            if ok:
                # Gap 1: persist checkpoint so a crash here doesn't redo this step
                _checkpoint_step(skill_id, run_id, i,
                                 step.get("tool_name", ""), result or "ok")
            else:
                # Step 4 — Failure fallback
                _log("SKILL_EXECUTION_FAILED", {
                    "failed_step":    i,
                    "tool_name":      step.get("tool_name"),
                    "error":          error,
                    "steps_done":     [r["step"] for r in step_results if r["ok"]],
                    "resume_hint":    f"Re-run with same run_id='{run_id}' to resume from step {i}",
                })
                _log("SKILL_FALLBACK")
                # Step 5 — Failure metrics
                _update_skill_metrics(skill_id, success=False)
                # NOTE: checkpoints from steps 0..i-1 are intentionally kept
                # so the next execution of the same run_id resumes from step i.
                return False

        # ── Step 6c — SKILL_EXECUTION_COMPLETED ──────────────────────────────
        _log("SKILL_EXECUTION_COMPLETED", {
            "steps_completed": len(step_results),
            "resumed_from":    resumed_from,
        })
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
