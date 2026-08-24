import os
import uuid
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import lancedb
import pyarrow as pa
from aja.config import PROJECT_ROOT, DATA_DIR

logger = logging.getLogger("aja.memory.secretary")

CHAT_TTL_DAYS = int(os.getenv("AJA_CHAT_TTL_DAYS", "30"))
CHAT_PRUNE_EVERY_N_WRITES = 25


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_tables_defensive(db) -> List[str]:
    try:
        tables = db.list_tables()
        if hasattr(tables, "tables"):
            return tables.tables
        return tables
    except Exception:
        return []


def sanitize_value(val: Any) -> str:
    """
    Sanitizes values for LanceDB SQL-like filter strings to prevent injection.
    """
    if isinstance(val, str):
        # Escape single quotes by doubling them
        safe_str = val.replace("'", "''")
        return f"'{safe_str}'"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, list):
        return "(" + ", ".join(sanitize_value(v) for v in val) + ")"
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


# --- Schemas ---

TASKS_SCHEMA = pa.schema(
    [
        ("task_id", pa.string()),
        ("title", pa.string()),
        ("context", pa.string()),
        ("owner", pa.string()),
        ("due_date", pa.string()),
        ("priority", pa.string()),
        ("status", pa.string()),
        ("created_at", pa.string()),
        ("updated_at", pa.string()),
        ("completion_note", pa.string()),
        ("metadata_json", pa.string()),
        ("vector", pa.list_(pa.float32(), 384)),
    ]
)

COMMUNICATIONS_SCHEMA = pa.schema(
    [
        ("message_id", pa.string()),
        ("recipient", pa.string()),
        ("content", pa.string()),
        ("draft_content", pa.string()),
        ("channel", pa.string()),
        ("delivery_status", pa.string()),
        ("approval_status", pa.string()),
        ("rejection_reason", pa.string()),
        ("created_at", pa.string()),
        ("updated_at", pa.string()),
    ]
)

APPROVALS_SCHEMA = pa.schema(
    [
        ("approval_id", pa.string()),
        ("kind", pa.string()),
        ("description", pa.string()),
        ("status", pa.string()),
        ("resolution_note", pa.string()),
        ("created_at", pa.string()),
        ("updated_at", pa.string()),
        ("metadata_json", pa.string()),
        ("vector", pa.list_(pa.float32(), 384)),
    ]
)

WORKERS_SCHEMA = pa.schema(
    [
        ("worker_id", pa.string()),
        ("hostname", pa.string()),
        ("pid", pa.int32()),
        ("last_heartbeat", pa.string()),
        ("status", pa.string()), # ONLINE, OFFLINE
        ("name", pa.string()), # Friendly name or type (e.g. 'autonomous-loop')
        # --- Registry fields (worker capability profiles) ---
        ("availability_status", pa.string()),   # active, paused, retired
        ("created_at", pa.string()),
        ("updated_at", pa.string()),
        ("worker_name", pa.string()),
        ("worker_type", pa.string()),
        ("description", pa.string()),
        ("model", pa.string()),
        ("preferred_task_types_json", pa.string()),
        ("blocked_task_types_json", pa.string()),
        ("supports_tests", pa.bool_()),
        ("supports_git_operations", pa.bool_()),
        ("supports_deployment", pa.bool_()),
        ("reliability_score", pa.float64()),
        ("execution_speed", pa.string()),       # fast, medium, slow
        ("cost_profile", pa.string()),          # free, subscription, pay_per_use
        ("approval_risk_level", pa.string()),
        ("historical_success_rate", pa.float64()),
        ("total_tasks_executed", pa.int32()),
        ("success_count", pa.int32()),
        ("fail_count", pa.int32()),
        ("primary_strengths_json", pa.string()),
        ("recent_failures_json", pa.string()),
    ]
)

WORKER_EXECUTIONS_SCHEMA = pa.schema(
    [
        ("log_id", pa.string()),
        ("worker_id", pa.string()),
        ("task_id", pa.string()),
        ("objective", pa.string()),
        ("success", pa.bool_()),
        ("duration_ms", pa.float64()),
        ("error", pa.string()),
        ("metadata_json", pa.string()),
        ("created_at", pa.string()),
    ]
)

RUNTIME_EVENTS_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("kind", pa.string()),
        ("target", pa.string()),
        ("status", pa.string()),
        ("message", pa.string()),
        ("command", pa.string()),
        ("metadata_json", pa.string()),
        ("timestamp", pa.string()),
    ]
)

MISSIONS_SCHEMA = pa.schema([
    pa.field("mission_id", pa.string()),
    pa.field("goal", pa.string()),
    pa.field("status", pa.string()), # PENDING, ACTIVE, AWAITING_APPROVAL, DONE, FAILED
    pa.field("priority", pa.int32()),
    pa.field("assigned_worker", pa.string()),
    pa.field("result_summary", pa.string()),
    pa.field("metadata_json", pa.string()),
    pa.field("created_at", pa.string()),
    pa.field("updated_at", pa.string()),
])

TERRITORY_KNOWLEDGE_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("path", pa.string()),
        ("content", pa.string()),
        ("metadata_json", pa.string()),
        ("updated_at", pa.string()),
        ("vector", pa.list_(pa.float32(), 384)),
    ]
)

CHAT_HISTORY_SCHEMA = pa.schema(
    [
        ("message_id", pa.string()),
        ("role", pa.string()),
        ("content", pa.string()),
        ("timestamp", pa.float64()),
        ("metadata_json", pa.string()),
    ]
)


class AJAMemory:
    """
    AJA Memory (Assistant of Joint Agents).
    Handles structured task persistence, obligations, and executive accountability.
    Utilizes LanceDB/Arrow for high-speed, zero-copy storage.
    """

    def __init__(self, db_path: str = str(DATA_DIR / "lancedb")):
        self.db = lancedb.connect(db_path)
        self._chat_write_counter = 0
        self._init_tables()

    def _init_tables(self):
        existing = list_tables_defensive(self.db)

        # 1. Tasks Table (Core obligations)
        if "aja_tasks" not in existing:
            self.db.create_table("aja_tasks", schema=TASKS_SCHEMA)

        # 2. Communications Table
        if "aja_communications" not in existing:
            self.db.create_table("aja_communications", schema=COMMUNICATIONS_SCHEMA)

        # 3. Approvals Table
        if "aja_approvals" not in existing:
            self.db.create_table("aja_approvals", schema=APPROVALS_SCHEMA)

        # 4. Workers/Swarm State
        if "aja_workers" not in existing:
            self.db.create_table("aja_workers", schema=WORKERS_SCHEMA)

        # 9. Worker execution history
        if "aja_worker_executions" not in existing:
            self.db.create_table("aja_worker_executions", schema=WORKER_EXECUTIONS_SCHEMA)

        # 5. Runtime Events
        if "aja_runtime_events" not in existing:
            self.db.create_table("aja_runtime_events", schema=RUNTIME_EVENTS_SCHEMA)

        # 6. Territory Knowledge (RAG)
        if "aja_territory_knowledge" not in existing:
            self.db.create_table(
                "aja_territory_knowledge", schema=TERRITORY_KNOWLEDGE_SCHEMA
            )

        # 7. Missions (AJA Executive Bridge)
        if "aja_missions" not in existing:
            self.db.create_table("aja_missions", schema=MISSIONS_SCHEMA)
            try:
                from aja.runtime.mission_journal import rebuild_all_mission_projections
                rebuild_all_mission_projections()
            except Exception as e:
                logger.warning(f"Failed to rebuild all mission projections on table creation: {e}")

        # 8. Chat History (Conversational Working-Set mirroring)
        if "aja_chat_history" not in existing:
            self.db.create_table("aja_chat_history", schema=CHAT_HISTORY_SCHEMA)

        # Schema evolution: backfill columns added after first release on
        # pre-existing tables (best-effort; never blocks startup).
        for tbl_name, schema in (
            ("aja_workers", WORKERS_SCHEMA),
            ("aja_communications", COMMUNICATIONS_SCHEMA),
        ):
            if tbl_name not in existing:
                continue
            try:
                table = self.db.open_table(tbl_name)
                existing_cols = {f.name for f in table.schema}
                missing = [f for f in schema if f.name not in existing_cols]
                if missing:
                    table.add_columns(pa.schema([pa.field(f.name, f.type) for f in missing]))
                    logger.info("Migrated %s schema: added %s", tbl_name, [f.name for f in missing])
            except Exception as e:
                logger.warning("%s schema migration check failed: %s", tbl_name, e)

    # --- Worker Management (Heartbeats) ---
    
    def publish_heartbeat(self, worker_id: str, status: str = "ONLINE", name: str = "unknown"):
        import socket
        import os
        table = self.db.open_table("aja_workers")
        now = datetime.now(timezone.utc).isoformat()

        # Cross-process-safe upsert keyed on worker_id. merge_insert performs
        # the existence check and insert atomically server-side, avoiding the
        # duplicate-worker race of a read-then-add sequence.
        #
        # when_matched_update_all() NULLs any schema column absent from the
        # input row, which would wipe worker profile fields (model, reliability,
        # strengths, ...) on every heartbeat. So we materialize a FULL row:
        # heartbeat columns from this process, all other columns carried over
        # from the existing row (or defaults for first-time workers).
        try:
            existing = (
                table.search()
                .where(f"worker_id = {sanitize_value(worker_id)}")
                .limit(1)
                .to_list()
            )
            row: Dict[str, Any] = {
                "worker_id": worker_id,
                "hostname": socket.gethostname(),
                "pid": int(os.getpid()),
                "last_heartbeat": now,
                "status": status,
                "name": name,
            }
            if existing:
                prev = existing[0]
                for field in WORKERS_SCHEMA.names:
                    if field not in row:
                        row[field] = prev.get(field)
            else:
                row.update(self._default_worker_fields(now))
            table.merge_insert(
                on="worker_id",
            ).when_matched_update_all().when_not_matched_insert_all().execute([row])
        except Exception as e:
            logger.error(f"Heartbeat publish failed for {worker_id}: {e}")

    @staticmethod
    def _default_worker_fields(now: str) -> Dict[str, Any]:
        """Full-column defaults for a brand-new worker row (merge-insert path)."""
        return {
            "availability_status": "active",
            "created_at": now,
            "updated_at": now,
            "worker_name": "",
            "worker_type": "",
            "description": "",
            "model": "",
            "preferred_task_types_json": "[]",
            "blocked_task_types_json": "[]",
            "supports_tests": False,
            "supports_git_operations": False,
            "supports_deployment": False,
            "reliability_score": 0.5,
            "execution_speed": "medium",
            "cost_profile": "subscription",
            "approval_risk_level": "medium",
            "historical_success_rate": 0.0,
            "total_tasks_executed": 0,
            "success_count": 0,
            "fail_count": 0,
            "primary_strengths_json": "[]",
            "recent_failures_json": "[]",
        }

    def get_active_workers(self, timeout_seconds: int = 30):
        table = self.db.open_table("aja_workers")
        workers = table.search().to_list()
        active = []
        now = datetime.now(timezone.utc)
        for w in workers:
            try:
                hb_str = w.get("last_heartbeat")
                if not hb_str:
                    continue
                hb = datetime.fromisoformat(hb_str)
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                if (now - hb).total_seconds() < timeout_seconds:
                    active.append(w)
            except Exception:
                pass
        return active

    # --- Task Management ---

    def create_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if data.get("owner") == "scheduler":
            tid = data.get("task_id") or f"JOB-{uuid.uuid4().hex[:6].upper()}"
            metadata = data.get("metadata", {})
            schedule_expr = metadata.get("schedule_expr", "")
            
            from aja.runtime.scheduler_journal import SchedulerJournal
            journal = SchedulerJournal()
            journal.emit("SCHEDULER_JOB_REGISTERED", {
                "job_id": tid,
                "goal": data.get("context", ""),
                "schedule_expr": schedule_expr
            })
            if metadata.get("paused", False):
                journal.emit("SCHEDULER_JOB_PAUSED", {"job_id": tid})
                
            return self.get_task(tid)

        tid = data.get("task_id") or uuid.uuid4().hex[:8]
        table = self.db.open_table("aja_tasks")
        row = {
            "task_id": tid,
            "title": data.get("title", "Untitled Task"),
            "context": data.get("context", ""),
            "owner": data.get("owner", "unknown"),
            "priority": data.get("priority", "medium"),
            "status": data.get("status", "pending"),
            "due_date": data.get("due_date"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "completion_note": "",
            "metadata_json": json.dumps(data.get("metadata", {})),
            "vector": [0.0] * 384,
        }
        table.add([row])
        return row

    # --- Mission Management (AJA Mission Executive) ---

    def create_mission(self, goal: str, priority: int = 1) -> Dict[str, Any]:
        mid = f"M-{uuid.uuid4().hex[:6].upper()}"
        from aja.runtime.mission_journal import MissionJournal
        journal = MissionJournal(mid)
        journal.emit("MISSION_CREATED", {
            "goal": goal,
            "priority": priority,
            "metadata": {}
        })
        return self.get_mission(mid)

    def list_missions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_missions")
        query = table.search()
        if status:
            query = query.where(f"status = {sanitize_value(status)}")
        return query.to_list()

    def update_mission(self, mission_id: str, updates: Dict[str, Any]) -> None:
        from aja.runtime.mission_journal import MissionJournal
        journal = MissionJournal(mission_id)
        
        # Determine status change
        if "status" in updates:
            journal.emit("MISSION_STATUS_CHANGED", {
                "from": self.get_mission(mission_id).get("status", "PENDING") if self.get_mission(mission_id) else "PENDING",
                "to": updates["status"]
            })
            
        # Determine completed state
        if "result_summary" in updates or updates.get("status") in ("DONE", "FAILED"):
            journal.emit("MISSION_COMPLETED", {
                "success": updates.get("status") == "DONE" or updates.get("status") != "FAILED",
                "result_summary": updates.get("result_summary", "")
            })
            
        # Determine run started
        if "active_run_id" in updates or "run_id" in updates:
            journal.emit("MISSION_RUN_STARTED", {
                "run_id": updates.get("active_run_id") or updates.get("run_id"),
                "trace_id": updates.get("active_trace_id") or updates.get("trace_id")
            })
            
        # Determine plan generated
        if "plan_id" in updates:
            journal.emit("MISSION_PLAN_GENERATED", {
                "plan_id": updates.get("plan_id")
            })

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_missions")
        results = (
            table.search()
            .where(f"mission_id = {sanitize_value(mission_id)}")
            .limit(1)
            .to_list()
        )
        return results[0] if results else None

    def list_tasks(
        self,
        status: Optional[str] = None,
        statuses: List[str] | None = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_tasks")
        query = table.search()
        if status:
            query = query.where(f"status = {sanitize_value(status)}")
        elif statuses:
            query = query.where(f"status IN {sanitize_value(statuses)}")
        return query.limit(limit).to_list()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_tasks")
        results = (
            table.search()
            .where(f"task_id = {sanitize_value(task_id)}")
            .limit(1)
            .to_list()
        )
        return results[0] if results else None

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        existing = self.get_task(task_id)
        if existing and existing.get("owner") == "scheduler":
            import time
            from aja.runtime.scheduler_journal import SchedulerJournal
            journal = SchedulerJournal()
            
            # Determine paused status from "status" or updates
            if "status" in updates:
                new_status = updates["status"]
                if new_status == "scheduled_paused":
                    journal.emit("SCHEDULER_JOB_PAUSED", {"job_id": task_id})
                elif new_status == "scheduled":
                    journal.emit("SCHEDULER_JOB_RESUMED", {"job_id": task_id})
                elif new_status == "archived":
                    journal.emit("SCHEDULER_JOB_DELETED", {"job_id": task_id})
                    
            # Check "metadata_json" to see if there is active run state, last_run, or paused updates
            if "metadata_json" in updates:
                meta = json.loads(updates["metadata_json"]) if isinstance(updates["metadata_json"], str) else updates["metadata_json"]
                if meta.get("paused") is True:
                    journal.emit("SCHEDULER_JOB_PAUSED", {"job_id": task_id})
                elif meta.get("paused") is False:
                    journal.emit("SCHEDULER_JOB_RESUMED", {"job_id": task_id})
                
                # Check for fired run
                if "active_run_id" in meta:
                    journal.emit("SCHEDULER_JOB_FIRED", {
                        "job_id": task_id,
                        "run_id": meta["active_run_id"],
                        "trace_id": meta.get("active_trace_id"),
                        "tick": meta.get("last_run_tick", 0),
                        "timestamp_ts": meta.get("last_run", time.time())
                    })
                elif "active_run_id" not in meta and existing.get("metadata_json"):
                    # If active_run_id was cleared, it indicates job completed!
                    exist_meta = json.loads(existing["metadata_json"]) if existing.get("metadata_json") else {}
                    if "active_run_id" in exist_meta:
                        journal.emit("SCHEDULER_JOB_COMPLETED", {
                            "job_id": task_id,
                            "run_id": exist_meta["active_run_id"],
                            "success": True
                        })
            return self.get_task(task_id)

        table = self.db.open_table("aja_tasks")
        updates["updated_at"] = utc_now()
        table.update(where=f"task_id = {sanitize_value(task_id)}", values=updates)
        return self.get_task(task_id)

    def complete_task(self, task_id: str, note: str = "") -> Dict[str, Any]:
        return self.update_task(
            task_id, {"status": "completed", "completion_note": note}
        )

    def archive_task(self, task_id: str, note: str = "") -> Dict[str, Any]:
        return self.update_task(
            task_id, {"status": "archived", "completion_note": note}
        )

    def snooze_task(self, task_id: str, until: str, reason: str = "") -> Dict[str, Any]:
        return self.update_task(
            task_id, {"status": "snoozed", "due_date": until, "completion_note": reason}
        )

    # --- Worker/Swarm Management ---

    # Fields accepted on a worker profile row; list-typed values are JSON-encoded.
    _WORKER_LIST_FIELDS = {
        "preferred_task_types": "preferred_task_types_json",
        "blocked_task_types": "blocked_task_types_json",
        "primary_strengths": "primary_strengths_json",
        "recent_failures": "recent_failures_json",
    }
    _WORKER_SCALAR_FIELDS = {
        "worker_id", "name", "worker_name", "worker_type", "description", "model",
        "availability_status", "created_at", "updated_at",
        "supports_tests", "supports_git_operations", "supports_deployment",
        "reliability_score", "execution_speed", "cost_profile",
        "approval_risk_level", "historical_success_rate", "total_tasks_executed",
        "success_count", "fail_count",
    }

    @staticmethod
    def _normalize_worker_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Decodes *_json columns back into lists and exposes worker_name."""
        out = dict(row)
        for field, col in AJAMemory._WORKER_LIST_FIELDS.items():
            raw = out.pop(col, None) if col in out else out.pop(field, None)
            if isinstance(raw, str):
                try:
                    out[field] = json.loads(raw)
                except (TypeError, ValueError):
                    out[field] = []
            elif raw is None:
                out[field] = []
            else:
                out[field] = raw
        if not out.get("worker_name"):
            out["worker_name"] = out.get("name") or out.get("worker_id")
        return out

    def list_workers(
        self, status: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_workers")
        query = table.search()
        if status:
            query = query.where(f"availability_status = {sanitize_value(status)}")
        return [self._normalize_worker_row(r) for r in query.limit(limit).to_list()]

    def get_worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_workers")
        results = (
            table.search()
            .where(f"worker_id = {sanitize_value(worker_id)}")
            .limit(1)
            .to_list()
        )
        return self._normalize_worker_row(results[0]) if results else None

    def create_worker(self, data: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_workers")
        wid = data.get("worker_id") or uuid.uuid4().hex[:8]
        existing = (
            table.search()
            .where(f"worker_id = {sanitize_value(wid)}")
            .limit(1)
            .to_list()
        )
        if existing:
            raise ValueError(f"Worker already exists: {wid}")
        now = utc_now()
        row: Dict[str, Any] = {
            "worker_id": wid,
            "hostname": "",
            "pid": 0,
            "last_heartbeat": "",
            "status": "OFFLINE",
            "name": data.get("worker_name") or data.get("name") or wid,
            "availability_status": data.get("availability_status", "active"),
            "created_at": now,
            "updated_at": now,
            "worker_name": data.get("worker_name") or "",
            "reliability_score": data.get("reliability_score", 0.5),
            "historical_success_rate": data.get("historical_success_rate", 0.0),
            "total_tasks_executed": data.get("total_tasks_executed", 0),
            "success_count": int(data.get("success_count") or 0),
            "fail_count": int(data.get("fail_count") or 0),
            "execution_speed": data.get("execution_speed", "medium"),
            "cost_profile": data.get("cost_profile", "subscription"),
            "approval_risk_level": data.get("approval_risk_level", "medium"),
            "supports_tests": bool(data.get("supports_tests", False)),
            "supports_git_operations": bool(data.get("supports_git_operations", False)),
            "supports_deployment": bool(data.get("supports_deployment", False)),
        }
        for field, col in self._WORKER_LIST_FIELDS.items():
            row[col] = json.dumps(data.get(field) or [])
        for scalar in ("worker_type", "description", "model"):
            row[scalar] = data.get(scalar) or ""
        table.add([row])
        return self._normalize_worker_row(self.get_worker(wid) or row)

    def update_worker(self, worker_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        if not self.get_worker(worker_id):
            raise KeyError(f"Worker not found: {worker_id}")
        table = self.db.open_table("aja_workers")
        values: Dict[str, Any] = {"updated_at": utc_now()}
        for key, val in updates.items():
            if key in self._WORKER_LIST_FIELDS:
                values[self._WORKER_LIST_FIELDS[key]] = json.dumps(val or [])
            elif key in self._WORKER_SCALAR_FIELDS and key != "worker_id":
                values[key] = val
        table.update(where=f"worker_id = {sanitize_value(worker_id)}", values=values)
        return self.get_worker(worker_id)

    def delete_worker(self, worker_id: str) -> bool:
        if not self.get_worker(worker_id):
            return False
        table = self.db.open_table("aja_workers")
        table.delete(f"worker_id = {sanitize_value(worker_id)}")
        return True

    def seed_default_workers(self) -> List[Dict[str, Any]]:
        """Seeds the registry with default worker profiles (idempotent)."""
        defaults = [
            {
                "worker_id": "local-default",
                "worker_name": "Local Default Worker",
                "worker_type": "local",
                "description": "On-host execution worker for direct tasks.",
                "model": "",
                "execution_speed": "medium",
                "cost_profile": "free",
                "primary_strengths": ["shell", "filesystem"],
                "preferred_task_types": ["code", "fix", "test", "maintenance"],
            },
            {
                "worker_id": "cloud-researcher",
                "worker_name": "Cloud Researcher",
                "worker_type": "research",
                "description": "Web research and summarization specialist.",
                "model": "",
                "execution_speed": "fast",
                "cost_profile": "subscription",
                "primary_strengths": ["web", "summarization"],
                "preferred_task_types": ["research", "documentation", "analysis"],
            },
        ]
        seeded = []
        for profile in defaults:
            try:
                seeded.append(self.create_worker(profile))
            except ValueError:
                pass  # already seeded
        return seeded

    # --- Worker Execution History ---

    def log_worker_execution(self, data: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_worker_executions")
        log_id = data.get("log_id") or uuid.uuid4().hex[:12]
        row = {
            "log_id": log_id,
            "worker_id": data.get("worker_id", ""),
            "task_id": data.get("task_id", ""),
            "objective": data.get("objective", ""),
            "success": bool(data.get("success", False)),
            "duration_ms": float(data.get("duration_ms") or 0.0),
            "error": data.get("error", ""),
            "metadata_json": json.dumps(data.get("metadata") or {}),
            "created_at": utc_now(),
        }
        table.add([row])
        # Fold outcome into the worker's track record using integer counters.
        # Accumulating a float percentage across updates drifts via rounding;
        # exact counts are drift-free and the display rate is derived from them.
        worker = self.get_worker(row["worker_id"])
        if worker:
            success_count = int(worker.get("success_count") or 0)
            fail_count = int(worker.get("fail_count") or 0)
            if row["success"]:
                success_count += 1
            else:
                fail_count += 1
            total = success_count + fail_count
            updates: Dict[str, Any] = {
                "total_tasks_executed": total,
                "success_count": success_count,
                "fail_count": fail_count,
                "historical_success_rate": (
                    round(100.0 * success_count / total, 2) if total else 0.0
                ),
            }
            if not row["success"]:
                failures = list(worker.get("recent_failures") or [])
                failures.insert(0, {"log_id": log_id, "error": row["error"], "at": row["created_at"]})
                updates.update(
                    reliability_score=max(0.0, float(worker.get("reliability_score") or 0.5) - 0.05),
                    recent_failures=failures[:5],
                )
            self.update_worker(row["worker_id"], updates)
        return row

    def get_worker_execution_history(self, worker_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_worker_executions")
        rows = (
            table.search()
            .where(f"worker_id = {sanitize_value(worker_id)}")
            .limit(limit)
            .to_list()
        )
        for r in rows:
            raw = r.pop("metadata_json", None)
            if isinstance(raw, str):
                try:
                    r["metadata"] = json.loads(raw)
                except (TypeError, ValueError):
                    r["metadata"] = {}
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return rows

    # --- Communication Management ---

    def create_communication(self, data: Dict[str, Any]) -> Dict[str, Any]:
        table = self.db.open_table("aja_communications")
        mid = uuid.uuid4().hex[:8]
        row = {
            "message_id": mid,
            "recipient": data.get("recipient", "unknown"),
            "content": data.get("content", ""),
            "draft_content": data.get("draft_content", ""),
            "channel": data.get("channel", "telegram"),
            "delivery_status": "pending",
            "approval_status": "awaiting",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        table.add([row])
        return row

    def list_communications(
        self,
        delivery_status: Optional[str] = None,
        approval_status: Optional[str] = None,
        pending_follow_up: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Lists communications with optional filters, matching the API bridge
        contract (delivery_status, approval_status, pending_follow_up, limit).

        Note: ``pending_follow_up`` is accepted for contract compatibility but
        not yet backed by a schema column; it is ignored by the query.
        """
        table = self.db.open_table("aja_communications")
        query = table.search()
        clauses = []
        if delivery_status:
            clauses.append(f"delivery_status = {sanitize_value(delivery_status)}")
        if approval_status:
            clauses.append(f"approval_status = {sanitize_value(approval_status)}")
        if clauses:
            query = query.where(" AND ".join(clauses))
        return query.limit(limit).to_list()

    def get_communication(self, message_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_communications")
        results = (
            table.search()
            .where(f"message_id = {sanitize_value(message_id)}")
            .limit(1)
            .to_list()
        )
        return results[0] if results else None

    def get_communication_history(
        self, recipient: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_communications")
        return (
            table.search()
            .where(f"recipient = {sanitize_value(recipient)}")
            .limit(limit)
            .to_list()
        )

    def approve_communication(self, message_id: str) -> Dict[str, Any]:
        return self.update_communication(message_id, {"approval_status": "approved"})

    def reject_communication(self, message_id: str, reason: str = "") -> Dict[str, Any]:
        return self.update_communication(
            message_id, {"approval_status": "rejected", "rejection_reason": reason}
        )

    def update_communication(
        self, message_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        table = self.db.open_table("aja_communications")
        updates["updated_at"] = utc_now()
        table.update(where=f"message_id = {sanitize_value(message_id)}", values=updates)
        return self.get_communication(message_id)

    def mark_communication_sent(
        self, message_id: str, note: str = ""
    ) -> Dict[str, Any]:
        # Prepend the delivery note instead of overwriting `content`, which
        # would destroy the original message body.
        updates: Dict[str, Any] = {"delivery_status": "sent"}
        if note:
            existing = self.get_communication(message_id)
            body = (existing or {}).get("content") or ""
            updates["content"] = f"{note}\n\n{body}" if body else note
        return self.update_communication(message_id, updates)

    # --- Approval Management ---

    def create_approval(self, data: Dict[str, Any]) -> str:
        aid = data.get("approval_id") or uuid.uuid4().hex[:8]
        table = self.db.open_table("aja_approvals")
        row = {
            "approval_id": aid,
            "kind": data.get("kind", "manual"),
            "description": data.get("description", ""),
            "status": "pending",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "metadata_json": json.dumps(data.get("metadata", {})),
            "vector": [0.0] * 384,
        }
        table.add([row])
        return aid

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        results = (
            table.search()
            .where(f"approval_id = {sanitize_value(approval_id)}")
            .limit(1)
            .to_list()
        )
        return results[0] if results else None

    def get_active_approval(self) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        results = table.search().where("status = 'pending'").limit(1).to_list()
        return results[0] if results else None

    def update_approval(
        self, approval_id: str, status: str, note: str = ""
    ) -> Dict[str, Any]:
        table = self.db.open_table("aja_approvals")
        table.update(
            where=f"approval_id = {sanitize_value(approval_id)}",
            values={"status": status, "resolution_note": note, "updated_at": utc_now()},
        )
        return self.get_approval(approval_id)

    def list_approvals(
        self, statuses: List[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_approvals")
        query = table.search()
        if statuses:
            query = query.where(f"status IN {sanitize_value(statuses)}")
        return query.limit(limit).to_list()

    def log_approval_audit(self, entry: Dict[str, Any]):
        self.record_scheduler_event(
            "approval_audit", entry.get("approval_id", "none"), entry
        )

    # --- Conversational working-set mirroring ---

    def mirror_chat_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        """
        Mirror a conversational chat turn directly to LanceDB in real-time.
        Appends a record batch to the chat_history LanceDB table.
        """
        import time
        table = self.db.open_table("aja_chat_history")
        row = {
            "message_id": uuid.uuid4().hex[:8],
            "role": role,
            "content": content,
            "timestamp": float(time.time()),
            "metadata_json": json.dumps(metadata or {}),
        }
        table.add([row])

        # Lightweight bounded-growth prune: every N writes, drop turns older
        # than the configured TTL. Never allowed to break the chat write.
        self._chat_write_counter += 1
        if self._chat_write_counter % CHAT_PRUNE_EVERY_N_WRITES == 0:
            try:
                self._prune_chat_history()
            except Exception as e:
                logger.warning("chat history prune failed: %s", e)

    def _prune_chat_history(self, ttl_days: Optional[int] = None) -> int:
        """Deletes chat turns older than ttl_days (bounded table growth).
        Mirrors the cleanup_old_tasks pattern; failures are logged, never
        raised so the chat write path stays healthy."""
        days = ttl_days if ttl_days is not None else CHAT_TTL_DAYS
        table = self.db.open_table("aja_chat_history")
        cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        try:
            rows = table.search().to_list()
            victims = [
                r.get("message_id")
                for r in rows
                if isinstance(r.get("timestamp"), (int, float))
                and r["timestamp"] < cutoff_ts
            ]
            victims = [v for v in victims if v]
            if not victims:
                return 0
            ids = ", ".join(sanitize_value(m) for m in victims)
            table.delete(f"message_id IN ({ids})")
            logger.info(
                "chat history prune: removed %d stale messages (ttl=%dd)",
                len(victims), days,
            )
            return len(victims)
        except Exception as e:
            logger.warning("chat history prune failed: %s", e)
            return 0

    def get_chat_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Retrieve conversational chat turns from the LanceDB chat_history table.

        Bounded fetch: pulls only a recent window (limit*10, min 100) from the
        table instead of scanning everything, then selects the newest `limit`
        rows and returns them oldest-first as callers expect.
        """
        table = self.db.open_table("aja_chat_history")
        window = max(int(limit) * 10, 100)
        results = table.search().limit(window).to_list()

        rows = sorted(results, key=lambda r: r.get("timestamp") or 0.0, reverse=True)[:limit]
        rows.sort(key=lambda r: r.get("timestamp") or 0.0)

        history = []
        for row in rows:
            metadata_json = row.get("metadata_json")
            history.append({
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "metadata": json.loads(metadata_json) if metadata_json else {}
            })
        return history

    # --- Runtime Events ---

    def add_runtime_event(self, event: Dict[str, Any]) -> str:
        return self.record_scheduler_event(
            kind=event.get("event_type", "INFO"),
            target=event.get("tool", "system"),
            metadata=event,
            status=True,
        )

    def record_scheduler_event(
        self, kind: str, target: str, metadata: Dict[str, Any], status: bool = True
    ) -> str:
        eid = uuid.uuid4().hex[:8]
        table = self.db.open_table("aja_runtime_events")
        row = {
            "event_id": eid,
            "kind": kind,
            "target": target,
            "status": "success" if status else "failed",
            "message": str(metadata.get("message", "")),
            "command": str(metadata.get("command", "")),
            "metadata_json": json.dumps(metadata),
            "timestamp": utc_now(),
        }
        table.add([row])
        return eid

    # --- Maintenance ---

    def cleanup_old_tasks(self, ttl_days: int = 30):
        """Deletes terminal tasks older than ttl_days (bounded table growth).
        Only rows that are BOTH terminal AND stale are removed; active work is
        never touched."""
        table = self.db.open_table("aja_tasks")
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=ttl_days)
        ).isoformat()
        try:
            before = table.search().to_list()
            victims = [r for r in before if self._is_stale_row(r, "updated_at", cutoff,
                {"done", "failed", "cancelled", "archived"})]
            if not victims:
                return 0
            ids = ", ".join(sanitize_value(r.get("task_id")) for r in victims)
            table.delete(f"task_id IN ({ids})")
            logger.info("cleanup_old_tasks: removed %d stale tasks (ttl=%dd)", len(victims), ttl_days)
            return len(victims)
        except Exception as e:
            logger.warning("cleanup_old_tasks failed: %s", e)
            return 0

    @staticmethod
    def _parse_iso_ts(value: Any) -> Optional[datetime]:
        """Parses ISO-8601 timestamps (incl. Z suffix and epoch numbers) into
        tz-aware UTC datetimes; returns None when unparseable."""
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                dt = datetime.fromtimestamp(float(s), tz=timezone.utc)
            except (TypeError, ValueError):
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def cleanup_old_approvals(self, ttl_days: int = 30):
        """Deletes resolved/expired/rejected approvals older than ttl_days."""
        table = self.db.open_table("aja_approvals")
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        terminal = {"resolved", "expired", "rejected"}
        try:
            before = table.search().to_list()
            seen_ids = set()
            victims = []
            for r in before:
                rid = r.get("approval_id")
                status = str(r.get("status", "")).lower()
                ts = self._parse_iso_ts(r.get("updated_at") or r.get("created_at"))
                stale = status in terminal and ts is not None and ts < cutoff_dt
                # Expiry lives inside metadata_json.approval_expires_at; it is
                # authoritative even without a terminal status.
                if not stale:
                    try:
                        meta = json.loads(r.get("metadata_json") or "{}")
                        if not isinstance(meta, dict):
                            meta = {}
                    except (TypeError, ValueError):
                        meta = {}
                    exp = self._parse_iso_ts(meta.get("approval_expires_at"))
                    if exp is not None and exp < cutoff_dt:
                        stale = True
                if stale and rid and rid not in seen_ids:
                    victims.append(r)
                    seen_ids.add(rid)
            if not victims:
                return 0
            ids = ", ".join(sanitize_value(r.get("approval_id")) for r in victims)
            table.delete(f"approval_id IN ({ids})")
            logger.info("cleanup_old_approvals: removed %d stale approvals (ttl=%dd)", len(victims), ttl_days)
            return len(victims)
        except Exception as e:
            logger.warning("cleanup_old_approvals failed: %s", e)
            return 0

    @staticmethod
    def _is_stale_row(row: Dict[str, Any], ts_col: str, cutoff_iso: str, terminal_statuses: set) -> bool:
        status = str(row.get("status", "")).lower()
        if status not in terminal_statuses:
            return False
        ts = row.get(ts_col) or ""
        return bool(ts) and ts < cutoff_iso

    def prune_events(self, max_rows: int = 10000):
        """
        Caps the runtime-events table at max_rows by deleting the oldest rows
        beyond the cap. Keeps the newest events for observability.
        """
        table = self.db.open_table("aja_runtime_events")
        try:
            rows = table.search().to_list()
            total = len(rows)
            if total <= max_rows:
                return 0
            rows.sort(key=lambda r: r.get("timestamp") or "")
            victims = rows[: total - max_rows]
            if not victims:
                return 0
            ids = ", ".join(sanitize_value(r.get("event_id")) for r in victims)
            table.delete(f"event_id IN ({ids})")
            logger.info("prune_events: removed %d old events (cap=%d)", len(victims), max_rows)
            return len(victims)
        except Exception as e:
            logger.warning("prune_events failed: %s", e)
            return 0

    # --- RAG & Territory Knowledge ---

    def add_knowledge_chunk(
        self, path: str, content: str, metadata: Dict[str, Any], vector: List[float]
    ):
        table = self.db.open_table("aja_territory_knowledge")
        row = {
            "id": uuid.uuid4().hex[:8],
            "path": path,
            "content": content,
            "metadata_json": json.dumps(metadata),
            "updated_at": utc_now(),
            "vector": vector,
        }
        table.add([row])

    def query_territory(
        self, query_vector: List[float], limit: int = 5
    ) -> List[Dict[str, Any]]:
        table = self.db.open_table("aja_territory_knowledge")
        return table.search(query_vector).limit(limit).to_list()

    def clear_territory_knowledge(self, path_prefix: str = ""):
        table = self.db.open_table("aja_territory_knowledge")
        if path_prefix:
            # Safely delete entries starting with the given path prefix
            # Note: LanceDB 'delete' uses SQL-like filters
            table.delete(f"path LIKE {sanitize_value(path_prefix + '%')}")
        else:
            table.delete("true")

    # --- Summaries ---

    def summary(self) -> Dict[str, Any]:
        existing = list_tables_defensive(self.db)
        counts = {}
        target_tables = [
            "aja_tasks",
            "aja_approvals",
            "aja_workers",
            "aja_communications",
            "aja_territory_knowledge",
            "aja_skills",
        ]
        for tbl in target_tables:
            if tbl in existing:
                try:
                    counts[tbl] = self.db.open_table(tbl).count_rows()
                except:
                    counts[tbl] = 0
            else:
                counts[tbl] = 0
        return counts

    def review(self, escalate: bool = False) -> str:
        stats = self.summary()
        return f"AJA System Review: {stats.get('aja_tasks', 0)} tasks, {stats.get('aja_workers', 0)} workers active."

    def generate_executive_review(
        self, kind: str = "morning", escalate: bool = False
    ) -> Dict[str, Any]:
        tasks = self.list_tasks(statuses=["pending", "active"], limit=5)
        return {
            "kind": kind,
            "timestamp": utc_now(),
            "active_tasks": len(tasks),
            "summary": f"AJA {kind.capitalize()} Review: {len(tasks)} tasks requiring attention.",
        }


# ── Standalone Helpers ────────────────────────────────────────────────────────


def format_tasks_for_mobile(tasks: List[Dict], review: str = "") -> str:
    lines = []
    if review:
        lines.append(f"💡 {review}")
        lines.append("")
    if not tasks:
        lines.append("No active tasks.")
    else:
        for t in tasks:
            status_icon = (
                "🟢"
                if t["status"] == "completed"
                else "⏳"
                if t["status"] == "active"
                else "⚪"
            )
            lines.append(f"{status_icon} [{t['task_id'][:4]}] {t['title']}")
            if t.get("due_date"):
                lines.append(f"   Due: {t['due_date']}")
            lines.append("")
    return "\n".join(lines).strip()


def format_communication_for_mobile(comm: Dict) -> str:
    status = comm.get("approval_status", "pending")
    icon = "✅" if status == "approved" else "❌" if status == "rejected" else "❓"
    return f"{icon} Message for: {comm['recipient']}\n---\n{comm.get('content') or comm.get('draft_content')}\n---\nStatus: {status}"


def parse_communication_intent(text: str, source: str = "unknown") -> Optional[Dict]:
    lowered = text.lower()
    if any(k in lowered for k in ["tell ", "message ", "ask ", "send message to "]):
        return {"recipient": "TBD", "content": text, "source": source}
    return None


def parse_task_intent(
    text: str, source: str = "unknown", owner: str = "unknown"
) -> Optional[Dict]:
    lowered = text.lower()
    task_triggers = [
        "todo:",
        "task:",
        "remind me to",
        "don't forget to",
        "remind me:",
        "need to",
        "i should",
    ]
    if any(lowered.startswith(t) for t in task_triggers) or (
        len(text.split()) > 3 and any(k in lowered for k in ["todo", "task", "remind"])
    ):
        return {"title": text, "priority": "medium", "source": source, "owner": owner}
    return None


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[AJAMemory] = None


def get_aja_memory() -> AJAMemory:
    global _instance
    if _instance is None:
        db_path = f"{DATA_DIR}/lancedb"
        _instance = AJAMemory(db_path)
    return _instance
