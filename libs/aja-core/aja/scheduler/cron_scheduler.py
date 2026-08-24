import asyncio
import logging
import os
import re
import time
import json
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from aja.observability.telemetry import TraceContextManager, get_trace_id
from aja.runtime.events import LanceRuntimeEventSink, RuntimeEventSink
from aja.runtime.task_store import LanceRuntimeTaskStore, RuntimeTaskStore

logger = logging.getLogger("aja.scheduler.cron_scheduler")

# Default hard interrupt limit for scheduled job execution. Configurable via
# the AJA_JOB_TIMEOUT_S environment variable (mirrors AJA_WORKER_TIMEOUT_S).
_DEFAULT_JOB_TIMEOUT_S = 600.0

# Output contract used to capture research mission reports.
_REPORT_CONTRACT = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}

# Keyword heuristic for classifying a scheduled goal as a RESEARCH mission.
_RESEARCH_KEYWORDS_RE = re.compile(
    r"\b(search|research|monitor|check|summarize|fetch|news|report|latest|changes)\b",
    re.IGNORECASE,
)


def get_job_timeout() -> float:
    """Returns the hard execution timeout (seconds) for scheduled jobs.

    Reads ``AJA_JOB_TIMEOUT_S`` from the environment; falls back to the
    600-second default when unset or non-numeric.
    """
    raw = os.getenv("AJA_JOB_TIMEOUT_S")
    if raw is None:
        return _DEFAULT_JOB_TIMEOUT_S
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_JOB_TIMEOUT_S
    except ValueError:
        return _DEFAULT_JOB_TIMEOUT_S


def is_research_goal(goal: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    """Heuristic: is this scheduled goal a RESEARCH mission?

    True when the job metadata explicitly flags ``research=True`` (override)
    OR the goal text matches a research keyword
    (search|research|monitor|check|summarize|fetch|news|report|latest|changes).
    """
    if isinstance(meta, dict) and meta.get("research") is True:
        return True
    return bool(goal and _RESEARCH_KEYWORDS_RE.search(goal))


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

    Timezone note: callers must pass NAIVE LOCAL time (``datetime.now()``).
    Operators specify cron times in their local wall clock, and one-shot jobs
    already persist naive-local ``run_at`` values; matching against UTC would
    fire recurring jobs offset by the host's UTC delta.
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


def _resolve_run_at(value: Any) -> datetime:
    """Resolves an ``at=`` value (datetime | ISO string | NL string) to naive-local."""
    from aja.utils.nl_time import parse_nl_time

    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        dt = None
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            dt = parse_nl_time(raw)
    if dt is None:
        raise ValueError(f"Could not parse one-shot time: {value!r}")
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0)


def create_reminder(
    task: str,
    when_raw: str,
    chat_id: Any = None,
    platform: Optional[str] = None,
    scheduler: Optional["CronScheduler"] = None,
) -> Optional[Dict[str, Any]]:
    """Creates a one-shot reminder job that publishes a bus event at fire time.

    INTEGRATOR WIRING (chat intent -> reminder):
        In the intent consumer (e.g. bridge.execute_telegram_command or
        gateway orchestrator route_intent), handle intent type == "reminder":

            from aja.scheduler.cron_scheduler import create_reminder

            job = create_reminder(
                task=intent["task"],
                when_raw=intent.get("when_raw", ""),
                chat_id=chat_id,
                platform="telegram",
            )
            if job is None:
                reply("I couldn't understand that time; try 'tomorrow 9am'.")
            else:
                reply(intent.get("response", "⏰ Reminder scheduled."))

        For type == "reminder_snooze" ({"minutes": N}), resolve the most
        recent reminder job id for the chat and call
        CronScheduler().snooze_reminder(job_id, N).

    Returns the created job dict, or None when ``when_raw`` is unparseable.
    """
    from aja.utils.nl_time import parse_nl_time

    run_at = parse_nl_time(when_raw) if when_raw else None
    if run_at is None:
        return None
    sched = scheduler or CronScheduler()
    meta_kwargs = {"reminder": True, "cleanup_after_fire": True}
    if chat_id is not None:
        meta_kwargs["chat_id"] = chat_id
    if platform is not None:
        meta_kwargs["platform"] = platform
    return sched.add_job(f"Reminder: {task}", at=run_at.isoformat(), **meta_kwargs)


class CronScheduler:
    """
    Deterministic cron and duration task scheduler for AJA runtime.
    Persists through an injected runtime task store and emits observable events
    through an injected event sink.
    Enforces a hard interrupt limit on scheduled executions
    (AJA_JOB_TIMEOUT_S env var, default 600 seconds).
    """
    
    def __init__(
        self,
        check_interval: float = 1.0,
        store: Optional[RuntimeTaskStore] = None,
        event_sink: Optional[RuntimeEventSink] = None,
    ):
        self.check_interval = check_interval
        self._running = False
        self._task = None
        self.store = store or LanceRuntimeTaskStore()
        self.event_sink = event_sink or LanceRuntimeEventSink()
        self._execution_tasks: Dict[str, asyncio.Task] = {}
        self._running_jobs: set[str] = set()
        self._tick: int = 0
        self._last_fire_tick: Dict[str, int] = {}
        # Compatibility for callers/tests that reached into the old concrete field.
        self.memory = self.store

    def _emit_event(
        self,
        event_type: str,
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        payload = {
            "event_type": event_type,
            "tool": "cron_scheduler",
            "message": message,
            "level": level,
            "trace_id": trace_id or get_trace_id(),
            "metadata": metadata or {},
        }
        self.event_sink.emit(payload)

    async def _emit_event_async(
        self,
        event_type: str,
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """Async variant of :meth:`_emit_event` that offloads the sink write.

        The Lance-backed event sink performs blocking disk IO; calling it from
        the tick loop would stall the shared event loop every second.
        """
        await asyncio.to_thread(
            self._emit_event,
            event_type,
            message,
            level=level,
            metadata=metadata,
            trace_id=trace_id,
        )

    def add_job(
        self,
        goal: str,
        schedule_expr: Optional[str] = None,
        *,
        at: Optional[str] = None,
        **meta_kwargs: Any,
    ) -> Dict[str, Any]:
        """Registers and persists a new scheduled job in LanceDB.

        Extra keyword arguments (e.g. ``research=True``) are merged into the
        job's persistent metadata dict.

        One-shot jobs: pass ``at=<ISO datetime or natural-language string>``
        (e.g. ``"2026-08-23T15:00:00"`` or ``"tomorrow 9am"``) instead of a
        cron/interval ``schedule_expr``. The job fires once when
        ``now >= run_at``, then is auto-disabled (or auto-deleted for
        reminders).
        """
        tid = f"JOB-{uuid.uuid4().hex[:6].upper()}"

        if at is not None:
            run_dt = _resolve_run_at(at)
            iso = run_dt.isoformat()
            schedule_expr = f"at:{iso}"
            meta_kwargs["one_shot"] = True
            meta_kwargs["run_at"] = iso
        else:
            # Verify schedule expression (either 5-field cron or 'every ...')
            is_cron = bool(schedule_expr) and len(schedule_expr.strip().split()) == 5
            is_dur = bool(schedule_expr) and parse_duration_to_seconds(schedule_expr) is not None

            if not (is_cron or is_dur):
                raise ValueError(f"Invalid schedule expression: '{schedule_expr}'. Must be a 5-field cron, 'every <num><unit>', or use at=... for one-shot jobs.")

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
                "paused": False,
                **meta_kwargs
            }
        }
        
        logger.info(f"Persisting scheduled job {tid} with schedule '{schedule_expr}' in LanceDB")
        return self.store.create_task(job_data)

    def pause_job(self, job_id: str) -> bool:
        """Pauses a scheduled job by updating its metadata or status."""
        job = self.store.get_task(job_id)
        if not job or job.get("owner") != "scheduler":
            return False
            
        meta = json.loads(job["metadata_json"]) if job.get("metadata_json") else {}
        meta["paused"] = True
        
        self.store.update_task(job_id, {
            "status": "scheduled_paused",
            "metadata_json": json.dumps(meta)
        })
        logger.info(f"Paused scheduled job {job_id}")
        return True

    def resume_job(self, job_id: str) -> bool:
        """Resumes a paused scheduled job."""
        job = self.store.get_task(job_id)
        if not job or job.get("owner") != "scheduler":
            return False
            
        meta = json.loads(job["metadata_json"]) if job.get("metadata_json") else {}
        meta["paused"] = False
        
        self.store.update_task(job_id, {
            "status": "scheduled",
            "metadata_json": json.dumps(meta)
        })
        logger.info(f"Resumed scheduled job {job_id}")
        return True

    def delete_job(self, job_id: str) -> bool:
        """Deletes a scheduled job by removing or archiving it."""
        job = self.store.get_task(job_id)
        if not job or job.get("owner") != "scheduler":
            return False
            
        self.store.update_task(job_id, {
            "status": "archived"
        })
        logger.info(f"Deleted/archived scheduled job {job_id}")
        return True

    def _fire_reminder(self, job_id: str, goal: str, meta: Dict[str, Any]) -> None:
        """Fires a reminder one-shot by publishing a telemetry-delivery bus event."""
        self._emit_event(
            "SCHEDULER_REMINDER_FIRED",
            f"Reminder due: {goal}",
            metadata={"job_id": job_id},
        )
        from aja.runtime.event_bus import bus, EVENTS

        delivery_event = (
            "MISSION_COMPLETED"
            if "MISSION_COMPLETED" in EVENTS
            else EVENTS.get("MISSION_RESULT", "MISSION_RESULT")
        )
        bus.publish(delivery_event, {
            "job_id": job_id,
            "message": f"⏰ Reminder: {goal}",
            "chat_id": meta.get("chat_id"),
            "platform": meta.get("platform"),
        })

    def snooze_reminder(self, job_id: str, minutes: int) -> bool:
        """Reschedules a fired/past one-shot reminder ``minutes`` into the future.

        Re-enables the job (status 'scheduled') with an updated run_at so it
        fires again through the normal tick loop.
        """
        if minutes <= 0:
            return False
        job = self.store.get_task(job_id)
        if not job or job.get("owner") != "scheduler":
            return False
        try:
            meta = json.loads(job.get("metadata_json") or "{}")
        except Exception:
            return False
        if not meta.get("one_shot"):
            return False
        if meta.get("cleanup_after_fire") and job.get("status") in (
            "disabled",
            "archived",
        ):
            # This reminder already fired and was archived/deleted; snoozing
            # it would resurrect a job the user never re-confirmed.
            logger.info(
                "Refusing to snooze fired-and-cleaned-up reminder %s", job_id
            )
            return False
        new_run_at = (datetime.now() + timedelta(minutes=minutes)).replace(microsecond=0)
        meta["run_at"] = new_run_at.isoformat()
        meta["schedule_expr"] = f"at:{meta['run_at']}"
        meta["paused"] = False
        self.store.update_task(job_id, {
            "status": "scheduled",
            "metadata_json": json.dumps(meta),
        })
        logger.info(f"Snoozed reminder job {job_id} for {minutes} minutes")
        return True

    def list_jobs(self) -> List[Dict[str, Any]]:
        """Returns all active and paused scheduled jobs from LanceDB."""
        all_tasks = self.store.list_tasks(statuses=["scheduled", "scheduled_paused"])
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

    async def _execute_job(self, job_id: str, goal: str, run_id: str, trace_id: str):
        """Executes a single job with a hard configurable timeout limit.

        Research missions (see :func:`is_research_goal`) additionally capture
        a structured report via an output contract (with graceful fallback to
        a plain synthesis), persist it as ``last_report`` in the job metadata,
        and publish it on the runtime event bus for platform delivery.
        """
        with TraceContextManager(trace_id):
            logger.info(f"Starting execution of scheduled task: '{goal}'")
            self._emit_event(
                "SCHEDULER_JOB_START",
                f"Executing scheduled job: {goal}",
                metadata={"job_id": job_id, "run_id": run_id},
            )

            # Lightweight briefing jobs run fully in-process (no SwarmEngine,
            # no timeout needed) — see aja.assistant.briefing.register_briefing_jobs.
            if self._read_job_meta(job_id).get("briefing"):
                await self._execute_briefing_job(job_id, goal)
                return

            from aja.orchestration.swarm import SwarmEngine
            from aja.config import CONFIG

            engine = SwarmEngine()
            timeout_s = get_job_timeout()
            is_research = is_research_goal(goal)
            if is_research:
                try:
                    meta = json.loads(
                        self.store.get_task(job_id).get("metadata_json") or "{}"
                    )
                except Exception:
                    meta = {}
                is_research = is_research_goal(goal, meta)

            report: Optional[str] = None

            try:
                # Enforce the hard interrupt limit (AJA_JOB_TIMEOUT_S, default 600s)
                if CONFIG.swarm_settings.direct_execution:
                    if is_research:
                        report = await asyncio.wait_for(
                            self._run_research_mission(engine, goal),
                            timeout=timeout_s * 2,  # contract attempt + fallback call
                        )
                    else:
                        await asyncio.wait_for(engine.execute_direct(goal), timeout=timeout_s)
                else:
                    await asyncio.wait_for(engine.plan_and_execute_batons(goal), timeout=timeout_s)

                logger.info(f"Successfully completed scheduled task: '{goal}'")
                self._emit_event(
                    "SCHEDULER_JOB_SUCCESS",
                    f"Successfully completed job: {goal}",
                    metadata={"job_id": job_id, "run_id": run_id},
                )

                if report:
                    self._deliver_research_report(job_id, goal, report)

            except asyncio.CancelledError:
                self._emit_event(
                    "SCHEDULER_JOB_CANCELLED",
                    f"Scheduled job cancelled: {goal}",
                    level="warning",
                    metadata={"job_id": job_id, "run_id": run_id},
                )
                raise
            except asyncio.TimeoutError:
                logger.error(
                    f"Execution of scheduled task '{goal}' timed out after {timeout_s:.0f} seconds!"
                )
                self._emit_event(
                    "SCHEDULER_JOB_TIMEOUT",
                    f"Job execution exceeded the {timeout_s:.0f}s limit (hard interrupted): {goal}",
                    level="error",
                    metadata={"job_id": job_id, "run_id": run_id},
                )
            except Exception as e:
                logger.exception(f"Error executing scheduled task '{goal}': {e}")
                self._emit_event(
                    "SCHEDULER_JOB_ERROR",
                    f"Job execution error: {e}",
                    level="error",
                    metadata={"job_id": job_id, "run_id": run_id},
                )
            finally:
                self._running_jobs.discard(job_id)
                self._execution_tasks.pop(job_id, None)

                def _finalize(meta: Dict[str, Any]) -> None:
                    meta.pop("active_run_id", None)
                    meta.pop("active_trace_id", None)
                    if report:
                        meta["last_report"] = report

                self._mutate_job_meta(job_id, _finalize)

    def _read_job_meta(self, job_id: str) -> Dict[str, Any]:
        """Safely reads a job's persistent metadata dict ({} on any failure)."""
        try:
            return json.loads(self.store.get_task(job_id).get("metadata_json") or "{}")
        except Exception:
            logger.warning("Could not read metadata for scheduled job %s", job_id)
            return {}

    def _mutate_job_meta(self, job_id: str, mutate) -> None:
        """Read-modify-write of a job's metadata with one retry.

        Guards against lost-update races with concurrent snooze/pause edits:
        the metadata is re-read fresh on each attempt, and a failed write is
        retried once before giving up (logged, never raised).
        """
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                job = self.store.get_task(job_id)
                if not job:
                    return
                meta = json.loads(job.get("metadata_json") or "{}")
                mutate(meta)
                self.store.update_task(
                    job_id, {"metadata_json": json.dumps(meta)}
                )
                return
            except Exception as e:
                last_exc = e
                time.sleep(0.05)
        logger.exception(
            "Failed to update scheduler metadata for %s after retry: %s",
            job_id,
            last_exc,
        )

    async def _execute_briefing_job(self, job_id: str, goal: str) -> None:
        """Runs an in-process briefing job: compose + publish on the bus.

        Deliberately avoids SwarmEngine — briefings are pure local composition.
        """
        logger.info(f"Executing in-process briefing job {job_id}: '{goal}'")
        try:
            from aja.assistant.briefing import send_briefing

            send_briefing()
            self._emit_event(
                "SCHEDULER_JOB_SUCCESS",
                f"Briefing published: {goal}",
                metadata={"job_id": job_id},
            )
        except Exception as e:
            logger.exception(f"Briefing job '{goal}' failed: {e}")
            self._emit_event(
                "SCHEDULER_JOB_ERROR",
                f"Briefing job error: {e}",
                level="error",
                metadata={"job_id": job_id},
            )
        finally:
            self._clear_run_metadata(job_id)

    def _clear_run_metadata(self, job_id: str) -> None:
        """Clears active run/trace bookkeeping after an in-process job run."""
        self._mutate_job_meta(
            job_id,
            lambda meta: (
                meta.pop("active_run_id", None),
                meta.pop("active_trace_id", None),
            ),
        )

    async def _run_research_mission(self, engine, goal: str) -> Optional[str]:
        """Runs a research mission capturing its report via an output contract.

        Mirrors NativeWorkerAdapter: attempt a structured synthesis; on
        StructuredOutputError (or any contract failure) fall back gracefully
        to a plain execute_direct call. Returns the summary text or None.
        """
        from aja.llm_structured import StructuredOutputError

        try:
            res = await engine.execute_direct(
                goal,
                output_contract=json.loads(json.dumps(_REPORT_CONTRACT)),
            )
            if isinstance(res, dict):
                result = res.get("result") or {}
                if isinstance(result, dict):
                    summary = result.get("summary")
                    if summary:
                        return str(summary)
        except StructuredOutputError:
            logger.warning(
                "Research job model cannot honor output contracts; "
                "falling back to plain synthesis."
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Structured research synthesis failed; falling back.")

        # Graceful fallback to a plain (non-contract) call.
        await engine.execute_direct(goal)
        return None

    def _deliver_research_report(self, job_id: str, goal: str, report: str) -> None:
        """Persists + broadcasts a research mission report for platform delivery."""
        self._emit_event(
            "SCHEDULER_JOB_REPORT",
            f"Research report for scheduled job {job_id}: {report}",
            metadata={"job_id": job_id, "goal": goal, "report": report},
        )

        from aja.runtime.event_bus import bus, EVENTS

        # MISSION_COMPLETED is the canonical delivery event; some runtimes do
        # not yet register it in EVENTS, so fall back to MISSION_RESULT which
        # gateway adapters subscribe to via EVENTS.values().
        delivery_event = (
            "MISSION_COMPLETED"
            if "MISSION_COMPLETED" in EVENTS
            else EVENTS.get("MISSION_RESULT", "MISSION_RESULT")
        )
        bus.publish(delivery_event, {
            "job_id": job_id,
            "goal": goal,
            "report": report,
            "message": f"Scheduled research complete ({goal}):\n{report}",
        })


    async def tick_loop(self):
        """Infinite loop checking schedules and triggering due tasks."""
        logger.info("Cron scheduler tick loop started")
        while self._running:
            try:
                self._tick += 1
                # Naive local time: cron expressions are operator-local and
                # one-shot run_at values are stored naive-local (see
                # match_cron_expr docstring).
                now_dt = datetime.now()
                now_ts = time.time()
                
                # Fetch only active scheduled tasks. Explicit high limit:
                # the store default is 50, which would permanently starve
                # job #51+ in large fleets.
                # Offloaded to a worker thread: LanceDB reads are disk-bound
                # and must not block the shared event loop every tick.
                scheduled_tasks = await asyncio.to_thread(
                    self.store.list_tasks, status="scheduled", limit=10000
                )
                
                for task in scheduled_tasks:
                    if task.get("owner") != "scheduler":
                        continue
                        
                    meta = json.loads(task["metadata_json"]) if task.get("metadata_json") else {}
                    if meta.get("paused", False):
                        continue
                        
                    expr = meta.get("schedule_expr", "")
                    last_run = meta.get("last_run", 0.0)
                    last_run_tick = meta.get("last_run_tick", 0)
                    
                    is_due = False
                    disable_after_fire = False

                    if meta.get("one_shot"):
                        # One-shot jobs bypass cron matching: fire when now >= run_at.
                        try:
                            run_dt = datetime.fromisoformat(meta.get("run_at") or "")
                        except (TypeError, ValueError):
                            logger.error(
                                f"One-shot job {task['task_id']} has invalid run_at "
                                f"{meta.get('run_at')!r}; disabling."
                            )
                            await asyncio.to_thread(
                                self.store.update_task,
                                task["task_id"],
                                {"status": "disabled"},
                            )
                            continue
                        if datetime.now() < run_dt:
                            continue
                        job_id = task["task_id"]
                        if meta.get("reminder"):
                            self._fire_reminder(job_id, task["context"], meta)
                            cleanup = meta.get("cleanup_after_fire", True)
                            await asyncio.to_thread(
                                self.store.update_task,
                                task["task_id"],
                                {"status": "archived" if cleanup else "disabled"},
                            )
                            continue
                        is_due = True
                        disable_after_fire = True
                    else:
                        # 1. Try simple duration
                        dur_secs = parse_duration_to_seconds(expr)
                        if dur_secs is not None:
                            dur_ticks = int(dur_secs)
                            if self._tick - last_run_tick >= dur_ticks:
                                is_due = True
                        else:
                            # 2. Try 5-field cron check
                            # Check minute boundary (we tick every second, so match once per minute boundary)
                            # We only match if standard cron matches and we haven't run in the last 59 seconds
                            if match_cron_expr(expr, now_dt):
                                if self._tick - last_run_tick >= 59:
                                    is_due = True
                                
                    if is_due:
                        job_id = task["task_id"]
                        if job_id in self._running_jobs:
                            await self._emit_event_async(
                                "SCHEDULER_JOB_SKIPPED_OVERLAP",
                                f"Skipped overlapping scheduled job: {task['context']}",
                                level="warning",
                                metadata={"job_id": job_id},
                            )
                            continue

                        logger.info(f"Triggering scheduled job {task['task_id']}: '{task['context']}'")
                        from aja.runtime.replay_guards import derive_run_id, derive_trace_id
                        run_id = derive_run_id(task["task_id"], self._tick)
                        trace_id = derive_trace_id(run_id)

                        # Immediately update last_run and last_run_tick to prevent double triggers
                        meta["last_run"] = now_ts
                        meta["last_run_tick"] = self._tick
                        meta["active_run_id"] = run_id
                        meta["active_trace_id"] = trace_id
                        updates = {"metadata_json": json.dumps(meta)}
                        if disable_after_fire:
                            updates["status"] = "disabled"
                        await asyncio.to_thread(
                            self.store.update_task, task["task_id"], updates
                        )

                        self._running_jobs.add(job_id)
                        await self._emit_event_async(
                            "SCHEDULER_JOB_DUE",
                            f"Scheduled job due: {task['context']}",
                            metadata={"job_id": job_id, "run_id": run_id},
                            trace_id=trace_id,
                        )

                        # Spawn task execution asynchronously in the background
                        exec_task = asyncio.create_task(
                            self._execute_job(job_id, task["context"], run_id, trace_id),
                            name=f"aja-scheduler-{job_id}-{run_id}",
                        )
                        self._execution_tasks[job_id] = exec_task

                        def _clear_owned_task(done_task: asyncio.Task, completed_job_id: str = job_id):
                            self._execution_tasks.pop(completed_job_id, None)
                            if done_task.cancelled():
                                self._running_jobs.discard(completed_job_id)

                        exec_task.add_done_callback(_clear_owned_task)
                        
            except Exception as e:
                logger.error(f"Error in scheduler tick loop: {e}")
                
            await asyncio.sleep(self.check_interval)

    def start(self):
        """Starts the scheduler in the current running event loop."""
        if not self._running:
            try:
                from aja.runtime.scheduler_journal import rebuild_scheduler_projections
                rebuild_scheduler_projections()
            except Exception as e:
                logger.warning(f"Failed to rebuild scheduler projections on start: {e}")
            self._running = True
            self._task = asyncio.create_task(self.tick_loop())
            logger.info("Scheduler started successfully")

    def stop(self):
        """Stops the running scheduler."""
        if self._running:
            self._running = False
            if self._task:
                self._task.cancel()
            for task in list(self._execution_tasks.values()):
                task.cancel()
            logger.info("Scheduler stopped successfully")

    async def stop_async(self):
        """Stops the scheduler and waits for owned tasks to settle."""
        self.stop()
        owned_tasks = [t for t in [self._task, *self._execution_tasks.values()] if t]
        if owned_tasks:
            await asyncio.gather(*owned_tasks, return_exceptions=True)
