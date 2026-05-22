import asyncio
import logging
import re
import time
import json
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from agentx.memory.secretary import AJAMemory, utc_now

logger = logging.getLogger("agentx.scheduler.cron_scheduler")

def match_cron_field(field_val: str, dt_val: int) -> bool:
    if field_val == "*":
        return True
    
    if "," in field_val:
        return any(match_cron_field(sub_field, dt_val) for sub_field in field_val.split(","))
        
    if "-" in field_val:
        start_str, end_str = field_val.split("-")
        step = 1
        if "/" in end_str:
            end_str, step_str = end_str.split("/")
            step = int(step_str)
        return dt_val in range(int(start_str), int(end_str) + 1, step)
        
    if "/" in field_val:
        base, step_str = field_val.split("/")
        step = int(step_str)
        if base == "*":
            return dt_val % step == 0
        else:
            return (dt_val - int(base)) % step == 0 and dt_val >= int(base)
            
    try:
        return int(field_val) == dt_val
    except ValueError:
        return False

def match_cron_expr(cron_expr: str, dt: datetime) -> bool:
    """
    Checks if a 5-field cron expression matches a given datetime.
    Fields: minute, hour, day of month, month, day of week (0-6, Sunday=0 or 7)
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
        
    minute, hour, dom, month, dow = fields
    
    # Python weekday(): Monday is 0, Sunday is 6.
    # Standard cron: Sunday is 0 or 7, Monday is 1, ..., Saturday=6
    cron_dow = dt.weekday() + 1
    if dt.weekday() == 6:  # Sunday
        dt_dow_options = [0, 7]
    else:
        dt_dow_options = [cron_dow]
        
    try:
        m_ok = match_cron_field(minute, dt.minute)
        h_ok = match_cron_field(hour, dt.hour)
        dom_ok = match_cron_field(dom, dt.day)
        mon_ok = match_cron_field(month, dt.month)
        dow_ok = any(match_cron_field(dow, opt) for opt in dt_dow_options)
        
        return m_ok and h_ok and dom_ok and mon_ok and dow_ok
    except Exception as e:
        logger.warning(f"Error matching cron field: {e}")
        return False

def parse_duration_to_seconds(expr: str) -> Optional[float]:
    """
    Parses expressions like 'every 2h', 'every 30m', 'every 10s' into float seconds.
    """
    expr = expr.strip().lower()
    if not expr.startswith("every "):
        return None
        
    val_part = expr[6:].strip()
    match = re.match(r"^(\d+)\s*(s|m|h|d|seconds|minutes|hours|days)$", val_part)
    if not match:
        return None
        
    num = int(match.group(1))
    unit = match.group(2)
    
    if unit in ("s", "seconds"):
        return float(num)
    elif unit in ("m", "minutes"):
        return float(num * 60)
    elif unit in ("h", "hours"):
        return float(num * 3600)
    elif unit in ("d", "days"):
        return float(num * 86400)
    return None


class CronScheduler:
    """
    Enterprise-grade Cron & Duration Task Scheduler for AgentX (AJA).
    Saves job definitions in the unified LanceDB `aja_tasks` table.
    Enforces a 3-minute hard interrupt limit on scheduled executions.
    """
    
    def __init__(self, check_interval: float = 1.0):
        self.check_interval = check_interval
        self._running = False
        self._task = None
        self.memory = AJAMemory()

    def add_job(self, goal: str, schedule_expr: str) -> Dict[str, Any]:
        """Registers and persists a new scheduled job in LanceDB."""
        tid = f"JOB-{uuid.uuid4().hex[:6].upper()}"
        
        # Verify schedule expression (either 5-field cron or 'every ...')
        is_cron = len(schedule_expr.strip().split()) == 5
        is_dur = parse_duration_to_seconds(schedule_expr) is not None
        
        if not (is_cron or is_dur):
            raise ValueError(f"Invalid schedule expression: '{schedule_expr}'. Must be a 5-field cron or 'every <num><unit>'.")
            
        job_data = {
            "task_id": tid,
            "title": f"Scheduled Job: {goal}",
            "context": goal,
            "owner": "scheduler",
            "status": "scheduled",
            "priority": "medium",
            "metadata": {
                "schedule_expr": schedule_expr,
                "last_run": 0.0,
                "paused": False
            }
        }
        
        logger.info(f"Persisting scheduled job {tid} with schedule '{schedule_expr}' in LanceDB")
        return self.memory.create_task(job_data)

    def pause_job(self, job_id: str) -> bool:
        """Pauses a scheduled job by updating its metadata or status."""
        job = self.memory.get_task(job_id)
        if not job or job.get("owner") != "scheduler":
            return False
            
        meta = json.loads(job["metadata_json"]) if job.get("metadata_json") else {}
        meta["paused"] = True
        
        self.memory.update_task(job_id, {
            "status": "scheduled_paused",
            "metadata_json": json.dumps(meta)
        })
        logger.info(f"Paused scheduled job {job_id}")
        return True

    def resume_job(self, job_id: str) -> bool:
        """Resumes a paused scheduled job."""
        job = self.memory.get_task(job_id)
        if not job or job.get("owner") != "scheduler":
            return False
            
        meta = json.loads(job["metadata_json"]) if job.get("metadata_json") else {}
        meta["paused"] = False
        
        self.memory.update_task(job_id, {
            "status": "scheduled",
            "metadata_json": json.dumps(meta)
        })
        logger.info(f"Resumed scheduled job {job_id}")
        return True

    def delete_job(self, job_id: str) -> bool:
        """Deletes a scheduled job by removing or archiving it."""
        job = self.memory.get_task(job_id)
        if not job or job.get("owner") != "scheduler":
            return False
            
        self.memory.update_task(job_id, {
            "status": "archived"
        })
        logger.info(f"Deleted/archived scheduled job {job_id}")
        return True

    def list_jobs(self) -> List[Dict[str, Any]]:
        """Returns all active and paused scheduled jobs from LanceDB."""
        all_tasks = self.memory.list_tasks(statuses=["scheduled", "scheduled_paused"])
        jobs = []
        for t in all_tasks:
            if t.get("owner") == "scheduler":
                meta = json.loads(t["metadata_json"]) if t.get("metadata_json") else {}
                jobs.append({
                    "job_id": t["task_id"],
                    "goal": t["context"],
                    "schedule_expr": meta.get("schedule_expr"),
                    "last_run": meta.get("last_run", 0.0),
                    "paused": meta.get("paused", False) or t["status"] == "scheduled_paused",
                    "status": t["status"]
                })
        return jobs

    async def _execute_job(self, job_id: str, goal: str):
        """Executes a single job with a hard 3-minute timeout limit."""
        logger.info(f"Starting execution of scheduled task: '{goal}'")
        
        # Emit event to LanceDB runtime events
        self.memory.add_runtime_event({
            "event_type": "SCHEDULER_JOB_START",
            "tool": "cron_scheduler",
            "message": f"Executing scheduled job: {goal}",
            "level": "info"
        })
        
        from agentx.orchestration.swarm import SwarmEngine
        from agentx.config import CONFIG
        engine = SwarmEngine()
        
        try:
            # Enforce the 3-minute hard interrupt limit
            if CONFIG.swarm_settings.direct_execution:
                await asyncio.wait_for(engine.execute_direct(goal), timeout=180.0)
            else:
                await asyncio.wait_for(engine.plan_and_execute_batons(goal), timeout=180.0)
            
            logger.info(f"Successfully completed scheduled task: '{goal}'")
            self.memory.add_runtime_event({
                "event_type": "SCHEDULER_JOB_SUCCESS",
                "tool": "cron_scheduler",
                "message": f"Successfully completed job: {goal}",
                "level": "info"
            })
        except asyncio.TimeoutError:
            logger.error(f"Execution of scheduled task '{goal}' timed out after 3 minutes!")
            self.memory.add_runtime_event({
                "event_type": "SCHEDULER_JOB_TIMEOUT",
                "tool": "cron_scheduler",
                "message": f"Job execution exceeded 3-minute limit (hard interrupted): {goal}",
                "level": "error"
            })
        except Exception as e:
            logger.exception(f"Error executing scheduled task '{goal}': {e}")
            self.memory.add_runtime_event({
                "event_type": "SCHEDULER_JOB_ERROR",
                "tool": "cron_scheduler",
                "message": f"Job execution error: {e}",
                "level": "error"
            })

    async def tick_loop(self):
        """Infinite loop checking schedules and triggering due tasks."""
        logger.info("Cron scheduler tick loop started")
        while self._running:
            try:
                now_dt = datetime.now()
                now_ts = time.time()
                
                # Fetch only active scheduled tasks
                scheduled_tasks = self.memory.list_tasks(status="scheduled")
                
                for task in scheduled_tasks:
                    if task.get("owner") != "scheduler":
                        continue
                        
                    meta = json.loads(task["metadata_json"]) if task.get("metadata_json") else {}
                    if meta.get("paused", False):
                        continue
                        
                    expr = meta.get("schedule_expr", "")
                    last_run = meta.get("last_run", 0.0)
                    
                    is_due = False
                    
                    # 1. Try simple duration
                    dur_secs = parse_duration_to_seconds(expr)
                    if dur_secs is not None:
                        if now_ts - last_run >= dur_secs:
                            is_due = True
                    else:
                        # 2. Try 5-field cron check
                        # Check minute boundary (we tick every second, so match once per minute boundary)
                        # We only match if standard cron matches and we haven't run in the last 59 seconds
                        if match_cron_expr(expr, now_dt):
                            if now_ts - last_run >= 59.0:
                                is_due = True
                                
                    if is_due:
                        logger.info(f"Triggering scheduled job {task['task_id']}: '{task['context']}'")
                        
                        # Immediately update last_run to prevent double triggers
                        meta["last_run"] = now_ts
                        self.memory.update_task(task["task_id"], {
                            "metadata_json": json.dumps(meta)
                        })
                        
                        # Spawn task execution asynchronously in the background
                        asyncio.create_task(self._execute_job(task["task_id"], task["context"]))
                        
            except Exception as e:
                logger.error(f"Error in scheduler tick loop: {e}")
                
            await asyncio.sleep(self.check_interval)

    def start(self):
        """Starts the scheduler in the current running event loop."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self.tick_loop())
            logger.info("Scheduler started successfully")

    def stop(self):
        """Stops the running scheduler."""
        if self._running:
            self._running = False
            if self._task:
                self._task.cancel()
            logger.info("Scheduler stopped successfully")
