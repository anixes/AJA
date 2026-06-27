"""
System invariant checker for AJA's LanceDB projections.

Validates structural correctness and business-rule constraints across all
LanceDB tables. Run directly (`python invariants.py`) or call
`check_invariants()` from tests.

Returns a list of violation strings. An empty list means all checks passed.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

import lancedb

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(".aja", "lancedb")

# Valid status values per table/column (mirrors the application constants).
VALID_TASK_STATUSES = {
    "pending",
    "active",
    "completed",
    "archived",
    "snoozed",
    "scheduled",
    "scheduled_paused",
}
VALID_MISSION_STATUSES = {
    "PENDING",
    "ACTIVE",
    "AWAITING_APPROVAL",
    "DONE",
    "FAILED",
    "REJECTED",
}
VALID_APPROVAL_STATUSES = {"pending", "resolved", "approved", "rejected"}
VALID_WORKER_STATUSES = {"ONLINE", "OFFLINE"}
VALID_COMM_DELIVERY_STATUSES = {"pending", "sent"}
VALID_COMM_APPROVAL_STATUSES = {"awaiting", "approved", "rejected"}
VALID_GOLDEN_EVALS = {"TRUE_SUCCESS", "PARTIAL_SUCCESS", "FALSE_SUCCESS"}
VALID_FAILURE_CAUSES = {
    "TOOL_ERROR",
    "DECISION_ERROR",
    "REASONING_ERROR",
    "CONTEXT_ERROR",
}
VALID_SKILL_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}
VALID_RULE_CONDITION_TYPES = {
    "AUTH_ERROR",
    "RATE_LIMIT",
    "TOOL_NOT_FOUND",
    "INVALID_INPUT",
    "GENERAL",
}
VALID_RULE_ACTIONS = {"ASK", "RETRY_WITH_DELAY", "REJECT"}
VALID_EVENT_STATUSES = {"success", "failed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_records(table) -> list[dict[str, Any]]:
    """Convert a LanceDB table to a list of plain dicts."""
    return table.to_arrow().to_pylist()


def _check_status_column(
    records: list[dict],
    id_col: str,
    status_col: str,
    valid: set[str],
    table_name: str,
    violations: list[str],
) -> None:
    """Append a violation for every row whose status_col value is not in valid."""
    for row in records:
        val = row.get(status_col)
        if val not in valid:
            violations.append(
                f"[{table_name}] row {row.get(id_col, '?')} has invalid "
                f"{status_col}={val!r}. Expected one of {sorted(valid)}."
            )


def _check_required_fields(
    records: list[dict],
    id_col: str,
    required_fields: list[str],
    table_name: str,
    violations: list[str],
) -> None:
    """Append a violation for every row that has a null/empty required field."""
    for row in records:
        rid = row.get(id_col, "?")
        for field in required_fields:
            val = row.get(field)
            if val is None or val == "":
                violations.append(
                    f"[{table_name}] row {rid} has missing required field {field!r}."
                )


def _check_iso_datetime(
    records: list[dict],
    id_col: str,
    dt_fields: list[str],
    table_name: str,
    violations: list[str],
) -> None:
    """Verify that datetime string fields are parseable ISO-8601 timestamps."""
    for row in records:
        rid = row.get(id_col, "?")
        for field in dt_fields:
            val = row.get(field)
            if not val:
                continue  # empty is caught by required-field checks if needed
            try:
                datetime.fromisoformat(val.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                violations.append(
                    f"[{table_name}] row {rid} field {field!r} is not a valid "
                    f"ISO datetime: {val!r}."
                )


def _check_valid_json(
    records: list[dict],
    id_col: str,
    json_fields: list[str],
    table_name: str,
    violations: list[str],
) -> None:
    """Verify that JSON-blob fields contain parseable JSON."""
    for row in records:
        rid = row.get(id_col, "?")
        for field in json_fields:
            val = row.get(field)
            if not val:
                continue
            try:
                json.loads(val)
            except (json.JSONDecodeError, TypeError):
                violations.append(
                    f"[{table_name}] row {rid} field {field!r} is not valid JSON: "
                    f"{str(val)[:80]!r}."
                )


def _check_uniqueness(
    records: list[dict],
    key_fields: list[str],
    table_name: str,
    violations: list[str],
) -> None:
    """Detect duplicate composite keys."""
    seen: dict[tuple, int] = {}
    for row in records:
        key = tuple(row.get(f) for f in key_fields)
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1:
            violations.append(
                f"[{table_name}] composite key {dict(zip(key_fields, key))} "
                f"appears {count} times (expected unique)."
            )


# ---------------------------------------------------------------------------
# Per-table invariant checks
# ---------------------------------------------------------------------------


def _check_aja_tasks(conn, violations: list[str]) -> None:
    table_name = "aja_tasks"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "task_id",
        ["task_id", "title", "status", "created_at"],
        table_name,
        violations,
    )
    _check_status_column(
        records, "task_id", "status", VALID_TASK_STATUSES, table_name, violations
    )
    _check_iso_datetime(
        records, "task_id", ["created_at", "updated_at"], table_name, violations
    )
    _check_valid_json(records, "task_id", ["metadata_json"], table_name, violations)
    _check_uniqueness(records, ["task_id"], table_name, violations)

    # Scheduler jobs must have a valid cron expression in metadata_json
    for row in records:
        if row.get("owner") == "scheduler":
            try:
                meta = json.loads(row.get("metadata_json") or "{}")
                if not meta.get("schedule_expr"):
                    violations.append(
                        f"[{table_name}] scheduler task {row.get('task_id', '?')} "
                        "has no schedule_expr in metadata_json."
                    )
            except (json.JSONDecodeError, TypeError):
                pass  # already flagged by _check_valid_json


def _check_aja_missions(conn, violations: list[str]) -> None:
    table_name = "aja_missions"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "mission_id",
        ["mission_id", "goal", "status", "created_at"],
        table_name,
        violations,
    )
    _check_status_column(
        records, "mission_id", "status", VALID_MISSION_STATUSES, table_name, violations
    )
    _check_iso_datetime(
        records, "mission_id", ["created_at", "updated_at"], table_name, violations
    )
    _check_valid_json(records, "mission_id", ["metadata_json"], table_name, violations)
    _check_uniqueness(records, ["mission_id"], table_name, violations)

    # Missions with ACTIVE status must have an assigned_worker
    for row in records:
        if row.get("status") == "ACTIVE" and not row.get("assigned_worker"):
            violations.append(
                f"[{table_name}] mission {row.get('mission_id', '?')} is ACTIVE "
                "but has no assigned_worker."
            )


def _check_aja_approvals(conn, violations: list[str]) -> None:
    table_name = "aja_approvals"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "approval_id",
        ["approval_id", "kind", "status", "created_at"],
        table_name,
        violations,
    )
    _check_status_column(
        records,
        "approval_id",
        "status",
        VALID_APPROVAL_STATUSES,
        table_name,
        violations,
    )
    _check_iso_datetime(
        records, "approval_id", ["created_at", "updated_at"], table_name, violations
    )
    _check_valid_json(records, "approval_id", ["metadata_json"], table_name, violations)
    _check_uniqueness(records, ["approval_id"], table_name, violations)

    # At most one approval may be in `pending` state at a time
    pending = [r for r in records if r.get("status") == "pending"]
    if len(pending) > 1:
        ids = [r.get("approval_id", "?") for r in pending]
        violations.append(
            f"[{table_name}] {len(pending)} approvals are simultaneously 'pending' "
            f"(expected at most 1). IDs: {ids}."
        )


def _check_aja_workers(conn, violations: list[str]) -> None:
    table_name = "aja_workers"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "worker_id",
        ["worker_id", "status", "last_heartbeat"],
        table_name,
        violations,
    )
    _check_status_column(
        records, "worker_id", "status", VALID_WORKER_STATUSES, table_name, violations
    )
    _check_iso_datetime(
        records, "worker_id", ["last_heartbeat"], table_name, violations
    )
    _check_uniqueness(records, ["worker_id"], table_name, violations)

    # ONLINE workers must have a heartbeat within the last 5 minutes
    now = datetime.now(timezone.utc)
    for row in records:
        if row.get("status") != "ONLINE":
            continue
        ts_str = row.get("last_heartbeat", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_minutes = (now - ts).total_seconds() / 60
            if age_minutes > 5:
                violations.append(
                    f"[{table_name}] worker {row.get('worker_id', '?')} is marked "
                    f"ONLINE but last heartbeat was {age_minutes:.1f} minutes ago."
                )
        except (ValueError, AttributeError):
            pass  # already caught by _check_iso_datetime


def _check_aja_communications(conn, violations: list[str]) -> None:
    table_name = "aja_communications"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "message_id",
        ["message_id", "recipient", "delivery_status", "approval_status"],
        table_name,
        violations,
    )
    _check_status_column(
        records,
        "message_id",
        "delivery_status",
        VALID_COMM_DELIVERY_STATUSES,
        table_name,
        violations,
    )
    _check_status_column(
        records,
        "message_id",
        "approval_status",
        VALID_COMM_APPROVAL_STATUSES,
        table_name,
        violations,
    )
    _check_iso_datetime(
        records, "message_id", ["created_at", "updated_at"], table_name, violations
    )
    _check_uniqueness(records, ["message_id"], table_name, violations)

    # A message cannot be `sent` while still `awaiting` approval
    for row in records:
        if (
            row.get("delivery_status") == "sent"
            and row.get("approval_status") == "awaiting"
        ):
            violations.append(
                f"[{table_name}] message {row.get('message_id', '?')} is marked "
                "'sent' but approval_status is still 'awaiting'."
            )


def _check_aja_runtime_events(conn, violations: list[str]) -> None:
    table_name = "aja_runtime_events"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "event_id",
        ["event_id", "kind", "status", "timestamp"],
        table_name,
        violations,
    )
    _check_status_column(
        records, "event_id", "status", VALID_EVENT_STATUSES, table_name, violations
    )
    _check_iso_datetime(records, "event_id", ["timestamp"], table_name, violations)
    _check_valid_json(records, "event_id", ["metadata_json"], table_name, violations)
    _check_uniqueness(records, ["event_id"], table_name, violations)


def _check_aja_territory_knowledge(conn, violations: list[str]) -> None:
    table_name = "aja_territory_knowledge"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records, "id", ["id", "path", "content"], table_name, violations
    )
    _check_iso_datetime(records, "id", ["updated_at"], table_name, violations)
    _check_uniqueness(records, ["id"], table_name, violations)


def _check_decision_logs(conn, violations: list[str]) -> None:
    table_name = "decision_logs"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "task_id",
        ["objective_hash", "decision_type", "outcome", "task_id"],
        table_name,
        violations,
    )
    _check_iso_datetime(records, "task_id", ["created_at"], table_name, violations)

    valid_outcomes = {"SUCCESS", "FAILURE", "FALLBACK"}
    _check_status_column(
        records, "task_id", "outcome", valid_outcomes, table_name, violations
    )

    # Confidence must be in [0.0, 1.0]
    for row in records:
        conf = row.get("confidence")
        if conf is not None and not (0.0 <= conf <= 1.0):
            violations.append(
                f"[{table_name}] task {row.get('task_id', '?')} has confidence "
                f"{conf} outside [0.0, 1.0]."
            )


def _check_decision_rules(conn, violations: list[str]) -> None:
    table_name = "decision_rules"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "rule_id",
        ["rule_id", "pattern", "condition_type", "action"],
        table_name,
        violations,
    )
    _check_status_column(
        records,
        "rule_id",
        "condition_type",
        VALID_RULE_CONDITION_TYPES,
        table_name,
        violations,
    )
    _check_status_column(
        records, "rule_id", "action", VALID_RULE_ACTIONS, table_name, violations
    )
    _check_iso_datetime(records, "rule_id", ["created_at"], table_name, violations)
    _check_uniqueness(records, ["rule_id"], table_name, violations)

    # (pattern, condition_type, action) must be unique — application deduplicates on insert
    _check_uniqueness(
        records, ["pattern", "condition_type", "action"], table_name, violations
    )


def _check_golden_tasks(conn, violations: list[str]) -> None:
    table_name = "golden_tasks"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "golden_id",
        ["golden_id", "objective", "expected_eval"],
        table_name,
        violations,
    )
    _check_status_column(
        records,
        "golden_id",
        "expected_eval",
        VALID_GOLDEN_EVALS,
        table_name,
        violations,
    )
    _check_iso_datetime(records, "golden_id", ["created_at"], table_name, violations)
    _check_uniqueness(records, ["golden_id"], table_name, violations)

    # run_count >= mismatch_count
    for row in records:
        rc = row.get("run_count", 0) or 0
        mc = row.get("mismatch_count", 0) or 0
        if mc > rc:
            violations.append(
                f"[{table_name}] golden task {row.get('golden_id', '?')} has "
                f"mismatch_count ({mc}) > run_count ({rc})."
            )


def _check_task_failures(conn, violations: list[str]) -> None:
    table_name = "task_failures"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "failure_id",
        ["failure_id", "task_id", "root_cause"],
        table_name,
        violations,
    )
    _check_status_column(
        records,
        "failure_id",
        "root_cause",
        VALID_FAILURE_CAUSES,
        table_name,
        violations,
    )
    _check_iso_datetime(records, "failure_id", ["created_at"], table_name, violations)
    _check_uniqueness(records, ["failure_id"], table_name, violations)


def _check_aja_skills(conn, violations: list[str]) -> None:
    table_name = "aja_skills"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "skill_id",
        ["skill_id", "name", "risk_level", "version"],
        table_name,
        violations,
    )
    _check_status_column(
        records,
        "skill_id",
        "risk_level",
        VALID_SKILL_RISK_LEVELS,
        table_name,
        violations,
    )
    _check_iso_datetime(
        records, "skill_id", ["created_at", "updated_at"], table_name, violations
    )
    _check_valid_json(
        records, "skill_id", ["tags_json", "tool_sequence_json"], table_name, violations
    )
    _check_uniqueness(records, ["skill_id"], table_name, violations)

    # confidence_score must be in [0.0, 1.0]
    for row in records:
        score = row.get("confidence_score")
        if score is not None and not (0.0 <= score <= 1.0):
            violations.append(
                f"[{table_name}] skill {row.get('skill_id', '?')} has "
                f"confidence_score {score} outside [0.0, 1.0]."
            )

    # version must be a positive integer
    for row in records:
        ver = row.get("version")
        if ver is not None and (not isinstance(ver, int) or ver < 1):
            violations.append(
                f"[{table_name}] skill {row.get('skill_id', '?')} has "
                f"invalid version={ver!r} (expected positive integer)."
            )


def _check_worker_registry(conn, violations: list[str]) -> None:
    table_name = "worker_registry"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "worker_id",
        ["worker_id", "name", "reliability"],
        table_name,
        violations,
    )
    _check_uniqueness(records, ["worker_id"], table_name, violations)

    for row in records:
        rel = row.get("reliability")
        if rel is not None and not (0.0 <= rel <= 1.0):
            violations.append(
                f"[{table_name}] worker {row.get('worker_id', '?')} has "
                f"reliability {rel} outside [0.0, 1.0]."
            )


def _check_skill_step_checkpoints(conn, violations: list[str]) -> None:
    table_name = "skill_step_checkpoints"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "checkpoint_id",
        ["checkpoint_id", "skill_id", "run_id", "step_index"],
        table_name,
        violations,
    )
    _check_iso_datetime(
        records, "checkpoint_id", ["completed_at"], table_name, violations
    )
    _check_uniqueness(records, ["checkpoint_id"], table_name, violations)
    # Composite key uniqueness
    _check_uniqueness(
        records, ["skill_id", "run_id", "step_index"], table_name, violations
    )


def _check_core_tasks(conn, violations: list[str]) -> None:
    table_name = "core_tasks"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "task_id",
        ["task_id", "run_id", "status", "created_at"],
        table_name,
        violations,
    )
    _check_iso_datetime(
        records, "task_id", ["created_at", "updated_at"], table_name, violations
    )
    _check_valid_json(records, "task_id", ["metadata_json"], table_name, violations)
    _check_uniqueness(records, ["task_id"], table_name, violations)

    # retry_count must be non-negative
    for row in records:
        rc = row.get("retry_count")
        if rc is not None and rc < 0:
            violations.append(
                f"[{table_name}] task {row.get('task_id', '?')} has "
                f"negative retry_count={rc}."
            )


def _check_core_tool_executions(conn, violations: list[str]) -> None:
    table_name = "core_tool_executions"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "execution_id",
        ["execution_id", "task_id", "tool_name", "status"],
        table_name,
        violations,
    )
    _check_iso_datetime(records, "execution_id", ["created_at"], table_name, violations)
    _check_valid_json(records, "execution_id", ["args_json"], table_name, violations)
    _check_uniqueness(records, ["execution_id"], table_name, violations)


def _check_core_plans(conn, violations: list[str]) -> None:
    table_name = "core_plans"
    if table_name not in conn.table_names():
        return
    records = _to_records(conn.open_table(table_name))

    _check_required_fields(
        records,
        "plan_id",
        ["plan_id", "goal", "status", "created_at"],
        table_name,
        violations,
    )
    _check_iso_datetime(records, "plan_id", ["created_at"], table_name, violations)
    _check_valid_json(records, "plan_id", ["steps_json"], table_name, violations)
    _check_uniqueness(records, ["plan_id"], table_name, violations)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_invariants(db_path: str = DB_PATH) -> list[str]:
    """
    Connect to the AJA LanceDB store and run all invariant checks.

    Returns a list of violation strings. An empty list means all checks passed.
    If the database does not exist yet, returns a single informational entry
    rather than raising an exception.
    """
    violations: list[str] = []

    if not os.path.exists(db_path):
        return [f"Database path does not exist: {db_path!r} — skipping checks."]

    try:
        conn = lancedb.connect(db_path)
    except Exception as exc:
        return [f"Failed to connect to LanceDB at {db_path!r}: {exc}"]

    checkers = [
        _check_aja_tasks,
        _check_aja_missions,
        _check_aja_approvals,
        _check_aja_workers,
        _check_aja_communications,
        _check_aja_runtime_events,
        _check_aja_territory_knowledge,
        _check_decision_logs,
        _check_decision_rules,
        _check_golden_tasks,
        _check_task_failures,
        _check_aja_skills,
        _check_worker_registry,
        _check_skill_step_checkpoints,
        _check_core_tasks,
        _check_core_tool_executions,
        _check_core_plans,
    ]

    for checker in checkers:
        try:
            checker(conn, violations)
        except Exception as exc:
            violations.append(f"[{checker.__name__}] unexpected error: {exc}")

    return violations


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    print(f"Checking invariants against: {db}\n")
    v = check_invariants(db)
    if not v:
        print("✓ All system invariants PASSED.")
        sys.exit(0)
    else:
        print(f"✗ {len(v)} INVARIANT VIOLATION(S) DETECTED:\n")
        for violation in v:
            print(f"  • {violation}")
        sys.exit(1)
