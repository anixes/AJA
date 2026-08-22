import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from aja.config import PROJECT_ROOT, DATA_DIR

logger = logging.getLogger(__name__)

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sql_quote(value: Any) -> str:
    """Escapes a value for safe interpolation into a LanceDB SQL predicate
    (single-quoted literal, embedded single quotes doubled)."""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "(" + ", ".join(_sql_quote(v) for v in value) + ")"
    safe = str(value).replace("'", "''")
    return f"'{safe}'"

class MissionState:
    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self.goal = ""
        self.status = "PENDING"
        self.priority = 1
        self.assigned_worker = ""
        self.result_summary = ""
        self.created_at = ""
        self.updated_at = ""
        self.metadata = {}
        self.active_run_id = None
        self.active_trace_id = None
        self.plan_id = None
        self.exploration_state = {}
        self.active_tool = None
        self.last_tool_result = {}
        self.last_error = {}

class MissionReducer:
    def reduce(self, events: List[Dict[str, Any]]) -> MissionState:
        if not events:
            raise ValueError("Cannot reduce empty events list")
        
        mission_id = events[0].get("mission_id")
        state = MissionState(mission_id)
        
        for event in events:
            self.apply(state, event)
            
        return state

    def apply(self, state: MissionState, event: Dict[str, Any]) -> None:
        event_type = event.get("event_type")
        timestamp = event.get("timestamp", utc_now())
        
        if event_type == "MISSION_CREATED":
            state.goal = event.get("goal", "")
            state.status = "PENDING"
            state.priority = event.get("priority", 1)
            state.created_at = timestamp
            state.updated_at = timestamp
            state.metadata = dict(event.get("metadata", {}))
            
        elif event_type == "MISSION_STATUS_CHANGED":
            state.status = event.get("to", "PENDING")
            state.updated_at = timestamp
            
        elif event_type == "MISSION_RUN_STARTED":
            state.active_run_id = event.get("run_id")
            state.active_trace_id = event.get("trace_id")
            state.status = "ACTIVE"
            state.updated_at = timestamp
            
        elif event_type == "MISSION_PLAN_GENERATED":
            state.plan_id = event.get("plan_id")
            state.updated_at = timestamp
            
        elif event_type == "MISSION_COMPLETED":
            state.status = "DONE" if event.get("success", True) else "FAILED"
            state.result_summary = event.get("result_summary", "")
            state.updated_at = timestamp
            
        elif event_type == "EXPLORATION_STATE_UPDATED":
            state.exploration_state = dict(event.get("exploration_state", {}))
            state.updated_at = timestamp

        elif event_type == "TOOL_CALLED":
            state.active_tool = event.get("tool")
            state.updated_at = timestamp

        elif event_type == "TOOL_COMPLETED":
            state.active_tool = None
            state.last_tool_result = {
                "tool": event.get("tool"),
                "success": event.get("success"),
                "exit_code": event.get("exit_code"),
                "env_state": event.get("env_state"),
            }
            state.updated_at = timestamp

        elif event_type == "TOOL_FAILED":
            state.active_tool = None
            state.last_error = {
                "tool": event.get("tool"),
                "error": event.get("error")
            }
            state.updated_at = timestamp

class MissionJournal:
    SHARD_EVENT_LIMIT = 5000

    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self.journal_dir = DATA_DIR / "missions"
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.journal_dir / f"mission_{mission_id}.jsonl"

    def _shard_if_needed(self) -> None:
        if not self.journal_path.exists():
            return
        # Cheap line count of the current file only; sealed shards are not re-read.
        line_count = 0
        with self.journal_path.open("r", encoding="utf-8") as f:
            for _ in f:
                line_count += 1
        if line_count >= self.SHARD_EVENT_LIMIT:
            shard_n = len(list(self.journal_dir.glob(f"mission_{self.mission_id}_shard_*.jsonl")))
            self.journal_path.rename(
                self.journal_dir / f"mission_{self.mission_id}_shard_{shard_n}.jsonl"
            )

    def emit(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._shard_if_needed()

        # 1. Compute the next sequence from the journal tail (O(1) per file)
        # instead of replaying the full history on every emit.
        seq = self._next_sequence()

        # 2. Build full versioned event
        event = {
            "event_type": event_type,
            "event_schema_version": "1.0",
            "mission_id": self.mission_id,
            "sequence": seq,
            "timestamp": utc_now(),
            **payload
        }

        # 3. Append to journal
        with self.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

        # 4. Write-through projection to LanceDB
        try:
            rebuild_mission_projections(self.mission_id)
        except Exception as e:
            # Tolerant write-through failure: secondary write failure should not block primary journal emission
            logger.warning(
                f"Failed to update write-through projection for mission {self.mission_id}: {e}"
            )

        return event

    def _last_sequence_in_file(self, path: Path) -> int:
        """Reads the last parseable line of a JSONL file and returns its sequence."""
        if not path.exists():
            return -1
        try:
            with path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                read_size = min(size, 65536)
                f.seek(max(0, size - read_size))
                tail_lines = f.read().decode("utf-8", errors="replace").splitlines()
            for line in reversed(tail_lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    return int(json.loads(line).get("sequence", -1))
                except (ValueError, json.JSONDecodeError):
                    continue
        except OSError as e:
            logger.warning("Failed to read journal tail from %s: %s", path, e)
        return -1

    def _next_sequence(self) -> int:
        highest = -1
        for shard in sorted(self.journal_dir.glob(f"mission_{self.mission_id}_shard_*.jsonl")):
            highest = max(highest, self._last_sequence_in_file(shard))
        highest = max(highest, self._last_sequence_in_file(self.journal_path))
        return highest + 1

    def read_events(self) -> List[Dict[str, Any]]:
        # Load from all shards first, then current journal
        events = []
        for shard in sorted(self.journal_dir.glob(f"mission_{self.mission_id}_shard_*.jsonl")):
            events.extend(self._load_jsonl(shard))
        if self.journal_path.exists():
            events.extend(self._load_jsonl(self.journal_path))
        return events

    def _load_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        """
        Loads events tolerantly: malformed lines (e.g. torn writes from a crash
        mid-append) are skipped with a warning instead of poisoning the whole
        journal permanently.
        """
        events = []
        with path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(
                        "Skipping corrupt journal line %s:%d (%s)", path.name, lineno, e
                    )
        return events

def rebuild_mission_projections(mission_id: str) -> None:
    journal = MissionJournal(mission_id)
    events = journal.read_events()
    if not events:
        return

    reducer = MissionReducer()
    state = reducer.reduce(events)

    from aja.runtime.lance_stores import LanceRuntimeStore
    mem = LanceRuntimeStore().memory
    table = mem.db.open_table("aja_missions")

    existing = table.search().where(f"mission_id = {_sql_quote(mission_id)}").limit(2).to_list()

    row = {
        "mission_id": state.mission_id,
        "goal": state.goal,
        "status": state.status,
        "priority": state.priority,
        "assigned_worker": state.assigned_worker or "",
        "result_summary": state.result_summary or "",
        "metadata_json": json.dumps(state.metadata),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }

    if existing:
        table.update(where=f"mission_id = {_sql_quote(mission_id)}", values=row)
    else:
        table.add([row])

def rebuild_all_mission_projections() -> None:
    journal_dir = DATA_DIR / "missions"
    if not journal_dir.exists():
        return

    shard_suffix = re.compile(r"_shard_\d+$")
    for p in journal_dir.glob("mission_*.jsonl"):
        mission_id = p.stem.replace("mission_", "")
        # Shard files are parts of another mission's journal, not missions of their own.
        if shard_suffix.search(mission_id):
            continue
        rebuild_mission_projections(mission_id)
