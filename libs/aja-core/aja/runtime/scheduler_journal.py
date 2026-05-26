import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from aja.config import PROJECT_ROOT

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

class JobState:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.goal = ""
        self.schedule_expr = ""
        self.last_run = 0.0
        self.last_run_tick = 0
        self.paused = False
        self.deleted = False
        self.active_run_id = None
        self.active_trace_id = None

class SchedulerReducer:
    def reduce(self, events: List[Dict[str, Any]]) -> Dict[str, JobState]:
        jobs: Dict[str, JobState] = {}
        for event in events:
            self.apply(jobs, event)
        return jobs

    def apply(self, jobs: Dict[str, JobState], event: Dict[str, Any]) -> None:
        event_type = event.get("event_type")
        job_id = event.get("job_id")
        if not job_id:
            return
            
        if job_id not in jobs:
            jobs[job_id] = JobState(job_id)
            
        job = jobs[job_id]
        
        if event_type == "SCHEDULER_JOB_REGISTERED":
            job.goal = event.get("goal", "")
            job.schedule_expr = event.get("schedule_expr", "")
            
        elif event_type == "SCHEDULER_JOB_FIRED":
            # standard ISO timestamp converted to unix or fallback to time.time()
            job.last_run = event.get("timestamp_ts", time.time())
            job.last_run_tick = event.get("tick", 0)
            job.active_run_id = event.get("run_id")
            job.active_trace_id = event.get("trace_id")
            
        elif event_type == "SCHEDULER_JOB_COMPLETED":
            if event.get("run_id") == job.active_run_id:
                job.active_run_id = None
                job.active_trace_id = None
                
        elif event_type == "SCHEDULER_JOB_PAUSED":
            job.paused = True
            
        elif event_type == "SCHEDULER_JOB_RESUMED":
            job.paused = False
            
        elif event_type == "SCHEDULER_JOB_DELETED":
            job.deleted = True

class SchedulerJournal:
    def __init__(self):
        self.journal_dir = PROJECT_ROOT / ".aja" / "scheduler"
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.journal_dir / "scheduler_journal.jsonl"

    def emit(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        events = self.read_events()
        seq = len(events)
        
        event = {
            "event_type": event_type,
            "event_schema_version": "1.0",
            "sequence": seq,
            "timestamp": utc_now(),
            "timestamp_ts": time.time(),
            **payload
        }
        
        with self.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
            
        try:
            rebuild_scheduler_projections()
        except Exception as e:
            import logging
            logging.getLogger("aja.runtime.scheduler_journal").warning(
                f"Failed to update write-through projection for scheduler jobs: {e}"
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

def rebuild_scheduler_projections() -> None:
    journal = SchedulerJournal()
    events = journal.read_events()
    
    reducer = SchedulerReducer()
    jobs = reducer.reduce(events)
    
    from aja.runtime.lance_stores import LanceRuntimeStore
    mem = LanceRuntimeStore().memory
    table = mem.db.open_table("aja_tasks")
    
    for job_id, job in jobs.items():
        existing = table.search().where(f"task_id = '{job_id}'").to_list()
        
        status = "archived" if job.deleted else ("scheduled_paused" if job.paused else "scheduled")
        
        meta = {
            "schedule_expr": job.schedule_expr,
            "last_run": job.last_run,
            "last_run_tick": job.last_run_tick,
            "paused": job.paused
        }
        if job.active_run_id:
            meta["active_run_id"] = job.active_run_id
        if job.active_trace_id:
            meta["active_trace_id"] = job.active_trace_id
            
        row = {
            "task_id": job.job_id,
            "title": f"Scheduled Job: {job.goal}",
            "context": job.goal,
            "owner": "scheduler",
            "status": status,
            "priority": "medium",
            "metadata_json": json.dumps(meta),
            "updated_at": utc_now(),
        }
        
        if existing:
            table.update(where=f"task_id = '{job_id}'", values=row)
        else:
            row["created_at"] = utc_now()
            row["due_date"] = ""
            row["completion_note"] = ""
            row["vector"] = [0.0] * 384
            table.add([row])
