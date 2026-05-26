import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from aja.config import PROJECT_ROOT

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

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

class MissionJournal:
    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self.journal_dir = PROJECT_ROOT / ".aja" / "missions"
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.journal_dir / f"mission_{mission_id}.jsonl"

    def emit(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 1. Read existing events to calculate sequence
        events = self.read_events()
        seq = len(events)
        
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
            import logging
            logging.getLogger("aja.runtime.mission_journal").warning(
                f"Failed to update write-through projection for mission {self.mission_id}: {e}"
            )
            
        return event

    def read_events(self) -> List[Dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        
        events = []
        with self.journal_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
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
    
    existing = table.search().where(f"mission_id = '{mission_id}'").to_list()
    
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
        table.update(where=f"mission_id = '{mission_id}'", values=row)
    else:
        table.add([row])

def rebuild_all_mission_projections() -> None:
    journal_dir = PROJECT_ROOT / ".aja" / "missions"
    if not journal_dir.exists():
        return
        
    for p in journal_dir.glob("mission_*.jsonl"):
        mission_id = p.stem.replace("mission_", "")
        rebuild_mission_projections(mission_id)
