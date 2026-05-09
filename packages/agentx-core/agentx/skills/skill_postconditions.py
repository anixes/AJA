"""
agentx/skills/skill_postconditions.py
======================================
Phase 9 — Gap 1: Skill correctness verification via post-condition validation.

Problem
-------
A skill can "succeed" (all tool steps returned OK) but still be INCORRECT:
  - email sent to wrong recipient
  - file written with corrupted content
  - record inserted with wrong ID

This module separates *execution success* from *correctness*.

Design
------
  - Postconditions are JSON-serialised assertions stored in skills.postconditions.
  - Each postcondition is a dict:  {type, target, expected, required}
  - Validators are registered in _VALIDATORS (pluggable — add new types without
    touching execute_skill).
  - Failure emits SKILL_POSTCONDITION_FAILED event and decrements confidence,
    but does NOT undo tool side-effects (post-conditions are read-only assertions).
  - `required: false` postconditions emit a WARNING event and do NOT count as
    correctness failures.

Postcondition schema (stored as JSON in skills.postconditions)
--------------------------------------------------------------
[
  {
    "type":     "key_present",        # type of check (see _VALIDATORS below)
    "target":   "recipient",          # path into step_results to inspect
    "expected": "alice@example.com",  # expected value / pattern
    "required": true                  # false = warning only, not a failure
  }
]

Supported types
---------------
  key_present    — target key exists in step_results
  value_equals   — step_results[target] == expected
  value_contains — expected substring in str(step_results[target])
  row_count_gte  — int(step_results[target]) >= int(expected)
  file_exists    — os.path.exists(expected) (target ignored)
  env_var_set    — os.environ.get(target) is truthy

Public API
----------
  validate_postconditions(skill, step_results, tracker, log_fn) -> (ok, failures)
  parse_postconditions(raw)  -> list[dict]
  add_postcondition(skill_id, postcondition_dict) -> bool
"""

import json
import os
import sqlite3
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# DB path
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return os.environ.get(
        "AGENTX_DB_PATH",
        os.path.join(".agentx", "aja_secretary.sqlite3"),
    )


# ---------------------------------------------------------------------------
# Schema bootstrap — adds postconditions column if not present
# ---------------------------------------------------------------------------

def _ensure_postconditions_column() -> None:
    try:
        path = _db_path()
        if not os.path.exists(path):
            return
        conn = sqlite3.connect(path)
        try:
            conn.execute("ALTER TABLE skills ADD COLUMN postconditions TEXT DEFAULT '[]'")
            conn.commit()
        except Exception:
            pass  # column already exists
        conn.close()
    except Exception:
        pass


_ensure_postconditions_column()


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------
# Each validator: (postcondition: dict, step_results: dict) -> (ok: bool, detail: str)

def _v_key_present(pc: dict, results: dict) -> tuple:
    key = pc.get("target", "")
    ok  = key in results
    return ok, (f"key '{key}' present in results" if ok
                else f"key '{key}' NOT found in step_results")


def _v_value_equals(pc: dict, results: dict) -> tuple:
    key      = pc.get("target", "")
    expected = str(pc.get("expected", ""))
    actual   = str(results.get(key, ""))
    ok       = (actual == expected)
    return ok, (f"{key}=={expected!r}" if ok
                else f"{key}=={actual!r} (expected {expected!r})")


def _v_value_contains(pc: dict, results: dict) -> tuple:
    key      = pc.get("target", "")
    expected = str(pc.get("expected", ""))
    actual   = str(results.get(key, ""))
    ok       = (expected in actual)
    return ok, (f"'{expected}' in {key}" if ok
                else f"'{expected}' NOT found in {key}={repr(actual)[:50]}")


def _v_row_count_gte(pc: dict, results: dict) -> tuple:
    key      = pc.get("target", "")
    minimum  = int(pc.get("expected", 1))
    try:
        actual = int(results.get(key, 0))
    except (TypeError, ValueError):
        return False, f"Cannot parse {key}={results.get(key)!r} as int"
    ok = (actual >= minimum)
    return ok, (f"{key}={actual} >= {minimum}" if ok
                else f"{key}={actual} < {minimum} (required)")


def _v_file_exists(pc: dict, results: dict) -> tuple:
    path = str(pc.get("expected", ""))
    ok   = os.path.exists(path)
    return ok, (f"file exists: {path}" if ok else f"file NOT found: {path}")


def _v_env_var_set(pc: dict, results: dict) -> tuple:
    name = str(pc.get("target", ""))
    ok   = bool(os.environ.get(name))
    return ok, (f"env {name} is set" if ok else f"env {name} is NOT set")


_VALIDATORS: dict = {
    "key_present":    _v_key_present,
    "value_equals":   _v_value_equals,
    "value_contains": _v_value_contains,
    "row_count_gte":  _v_row_count_gte,
    "file_exists":    _v_file_exists,
    "env_var_set":    _v_env_var_set,
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_postconditions(raw) -> list:
    """
    Parse postconditions from a skills row.
    Accepts: JSON string, list, or None.
    Returns a list of dicts (empty on failure or None input).
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Flatten step_results into a single dict for postcondition lookups
# ---------------------------------------------------------------------------

def _flatten_results(step_results: list) -> dict:
    """
    Merge all step result payloads into a single flat dict.

    step_results is a list of dicts from execute_skill(), each may have:
      {"step": 0, "tool": "fetch_data", "ok": True, "result": "{...}"}

    We parse any JSON-string "result" values and merge them in.
    Last-writer wins on key collisions (later steps override earlier ones).
    """
    flat = {}
    for step in step_results:
        if not isinstance(step, dict):
            continue
        flat[f"step_{step.get('step', '?')}_tool"] = step.get("tool", "")
        flat[f"step_{step.get('step', '?')}_ok"]   = step.get("ok", False)
        raw = step.get("result") or step.get("value", "")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    flat.update(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(raw, dict):
            flat.update(raw)
    return flat


# ---------------------------------------------------------------------------
# Metrics helper — update confidence after postcondition failure
# ---------------------------------------------------------------------------

def _penalise_confidence(skill_id: str) -> None:
    """Increment failure_count and recalculate confidence_score on correctness failure."""
    try:
        path = _db_path()
        if not os.path.exists(path):
            return
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT success_count, failure_count FROM skills WHERE id = ?",
                (skill_id,)
            ).fetchone()
            if row is None:
                return
            s    = row["success_count"]
            f    = row["failure_count"] + 1
            conf = round(s / (s + f), 4) if (s + f) > 0 else 1.0
            conn.execute(
                """UPDATE skills SET failure_count=?, confidence_score=?, updated_at=?
                   WHERE id=?""",
                (f, conf, datetime.now(timezone.utc).isoformat(), skill_id),
            )
    except Exception as e:
        print(f"[Postconditions] _penalise_confidence() error: {e}")


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def validate_postconditions(
    skill:        dict,
    step_results: list,
    tracker=None,
    log_fn=None,
) -> tuple:
    """
    Validate post-execution correctness assertions for a skill.

    Parameters
    ----------
    skill        : dict from skill_store (must include id, name, postconditions)
    step_results : list of step outcome dicts from execute_skill() loop
    tracker      : optional tracker module for structured event logging
    log_fn       : optional callable(event, extra) for custom logging
                   (used by execute_skill to share its _log context)

    Returns
    -------
    (ok: bool, failures: list[str])
      ok=True  — all *required* postconditions passed (non-required are warnings only)
      ok=False — at least one required postcondition failed

    Side effects
    ------------
    - Emits SKILL_POSTCONDITION_FAILED or SKILL_POSTCONDITION_PASSED event.
    - On required failure: calls _penalise_confidence() to update skill metrics.
    - Never raises — all exceptions caught per module contract.
    """
    skill_id   = skill.get("id", "unknown")
    skill_name = skill.get("name", skill_id)

    def _emit(event: str, extra: dict = None):
        payload = {"skill_id": skill_id, "skill_name": skill_name, **(extra or {})}
        msg     = f"[Postconditions] {event}  skill='{skill_name}'"
        if extra:
            msg += f"  detail={extra}"
        print(msg)
        if log_fn:
            try:
                log_fn(event, extra or {})
            except Exception:
                pass
        if tracker:
            try:
                tracker.log_event(event, payload)
            except Exception:
                pass

    postconditions = parse_postconditions(skill.get("postconditions"))

    if not postconditions:
        return True, []   # no assertions declared → trivially correct

    flat    = _flatten_results(step_results)
    all_ok  = True
    failures = []

    for pc in postconditions:
        pc_type   = pc.get("type", "unknown")
        required  = pc.get("required", True)
        validator = _VALIDATORS.get(pc_type)

        if validator is None:
            # Unknown postcondition type — warn but do not fail
            _emit("SKILL_POSTCONDITION_UNKNOWN", {
                "type": pc_type, "postcondition": pc
            })
            continue

        try:
            ok, detail = validator(pc, flat)
        except Exception as e:
            ok, detail = False, f"Validator error: {e}"

        if ok:
            _emit("SKILL_POSTCONDITION_PASSED", {"type": pc_type, "detail": detail})
        else:
            if required:
                _emit("SKILL_POSTCONDITION_FAILED", {
                    "type": pc_type, "detail": detail, "required": True,
                    "impact": "confidence_score will be decremented",
                })
                failures.append(f"[{pc_type}] {detail}")
                all_ok = False
            else:
                # non-required → warn only, does not affect ok
                _emit("SKILL_POSTCONDITION_WARNING", {
                    "type": pc_type, "detail": detail, "required": False,
                })

    if not all_ok:
        _penalise_confidence(skill_id)

    return all_ok, failures


# ---------------------------------------------------------------------------
# Mutation helper — add a postcondition to an existing skill
# ---------------------------------------------------------------------------

def add_postcondition(skill_id: str, postcondition: dict) -> bool:
    """
    Append a postcondition dict to an existing skill's postconditions list.

    postcondition must have at minimum: {"type": str, "target": str, "expected": ...}
    "required" defaults to True if absent.

    Returns True on success, False on error (e.g. skill not found).
    """
    postcondition.setdefault("required", True)

    required_fields = {"type", "target", "expected"}
    if not required_fields.issubset(postcondition.keys()):
        print(f"[Postconditions] add_postcondition: missing fields "
              f"{required_fields - postcondition.keys()}")
        return False

    try:
        path = _db_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT postconditions FROM skills WHERE id = ?", (skill_id,)
            ).fetchone()
            if row is None:
                print(f"[Postconditions] Skill not found: {skill_id}")
                return False

            existing = parse_postconditions(row["postconditions"])
            existing.append(postcondition)
            conn.execute(
                "UPDATE skills SET postconditions=?, updated_at=? WHERE id=?",
                (json.dumps(existing), datetime.now(timezone.utc).isoformat(), skill_id),
            )
        print(f"[Postconditions] Added {postcondition['type']} postcondition to {skill_id[:12]}...")
        return True
    except Exception as e:
        print(f"[Postconditions] add_postcondition() error: {e}")
        return False
