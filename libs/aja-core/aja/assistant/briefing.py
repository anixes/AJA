"""AJA Daily Briefing — composes and delivers the ambient morning/evening digest.

Ingredients (each degrades gracefully to "(unavailable)" or omission):
  * Overdue / today tasks      — memory.list_tasks (secretary task helpers)
  * Calendar events (next 24h) — aja.calendar.graph_sync.events_between
  * Upcoming reminders         — CronScheduler one-shot reminder jobs
  * Priority Focus             — aja.api.services.priority_engine.run_priority_engine
  * Overnight Research         — research job ``last_report`` metadata (Phase 10)

Delivery publishes ``EVENTS["MISSION_COMPLETED"]`` with ``{"message": md}``
so existing Telegram/Discord telemetry tails deliver it unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BRIEFING_GOAL = "AJA Briefing"

_RESEARCH_WINDOW_H = 24
_REPORT_TRUNCATE_CHARS = 300
_REMINDER_PREFIX = "Reminder: "


# --------------------------------------------------------------------------- helpers

def _truncate(text: str, limit: int = _REPORT_TRUNCATE_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _parse_due_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19] if fmt.startswith("%Y-%m-%dT") else raw[:10], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _default_memory():
    from aja.memory.secretary import AJAMemory

    return AJAMemory()


def _default_scheduler():
    from aja.scheduler.cron_scheduler import CronScheduler

    return CronScheduler()


def _load_jobs(scheduler) -> List[Tuple[str, Dict[str, Any]]]:
    """Returns [(goal, meta)] for every non-paused scheduler-owned job."""
    jobs: List[Tuple[str, Dict[str, Any]]] = []
    for t in scheduler.store.list_tasks(statuses=["scheduled", "scheduled_paused"]):
        if t.get("owner") != "scheduler":
            continue
        try:
            meta = json.loads(t.get("metadata_json") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict):
            jobs.append((t.get("context") or "", meta))
    return jobs


def _section(header: str, lines: List[str], unavailable: bool) -> Optional[str]:
    if unavailable:
        return f"## {header}\n(unavailable)"
    if not lines:
        return None
    return f"## {header}\n" + "\n".join(lines)


# --------------------------------------------------------------------------- ingredients

def _task_sections(
    memory, now: datetime
) -> Tuple[List[str], List[str], int, int, bool]:
    """Returns (overdue_lines, today_task_lines, n_overdue, n_today_tasks, failed)."""
    if memory is None:
        memory = _default_memory()
    tasks = memory.list_tasks(statuses=["pending", "active"])
    overdue_lines: List[str] = []
    today_lines: List[str] = []
    for task in tasks or []:
        due = _parse_due_date(task.get("due_date"))
        if due is None:
            continue
        title = task.get("title") or task.get("context") or "Untitled task"
        if due.date() < now.date():
            days_late = (now.date() - due.date()).days
            overdue_lines.append(f"- **{title}** — {days_late} day(s) late")
        elif due.date() == now.date():
            time_part = f" at {due.strftime('%H:%M')}" if len(str(task.get("due_date"))) > 10 else ""
            today_lines.append(f"- **{title}**{time_part}")
    return overdue_lines, today_lines, len(overdue_lines), len(today_lines), False


def _calendar_events(graph, now_utc: datetime) -> List[Dict[str, Any]]:
    from aja.calendar.graph_sync import events_between

    start = now_utc.isoformat()
    end = (now_utc + timedelta(hours=24)).isoformat()
    rows = events_between(start, end, graph=graph)
    events: List[Dict[str, Any]] = []
    for row in rows:
        props = row.get("properties") or {}
        start_dt = None
        epoch = row.get("start_epoch")
        if epoch:
            start_dt = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        events.append(
            {
                "title": props.get("title")
                or str(row.get("name") or "").removeprefix("[cal] "),
                "start_dt": start_dt,
                "location": props.get("location") or "",
            }
        )
    return events


def _reminder_lines(scheduler, now: datetime) -> List[str]:
    if scheduler is None:
        scheduler = _default_scheduler()
    horizon = now + timedelta(hours=24)
    upcoming = []
    for goal, meta in _load_jobs(scheduler):
        if not meta.get("reminder") or not meta.get("one_shot"):
            continue
        try:
            run_at = datetime.fromisoformat(meta.get("run_at") or "")
        except (TypeError, ValueError):
            continue
        if now <= run_at <= horizon:
            label = goal.removeprefix(_REMINDER_PREFIX)
            upcoming.append((run_at, label))
    upcoming.sort(key=lambda pair: pair[0])
    return [f"- ⏰ **{label}** at {run_at.strftime('%H:%M')}" for run_at, label in upcoming]


def _priority_lines(memory) -> List[str]:
    from aja.api.services.priority_engine import run_priority_engine

    result = run_priority_engine(memory)
    lines = []
    for task in result.get("top3") or []:
        title = task.get("title") or task.get("context") or "Untitled task"
        lines.append(
            f"- **{title}** — score {task.get('priority_score')}"
            f" ({task.get('urgency_tier')} tier)"
        )
    return lines


def _research_lines(scheduler, now: datetime) -> List[str]:
    from aja.scheduler.cron_scheduler import is_research_goal

    cutoff = time.time() - _RESEARCH_WINDOW_H * 3600
    lines = []
    for goal, meta in _load_jobs(scheduler):
        report = meta.get("last_report")
        if not report or not (meta.get("research") or is_research_goal(goal, meta)):
            continue
        if float(meta.get("last_run") or 0) < cutoff:
            continue
        lines.append(f"- **{goal}** — {_truncate(str(report))}")
    return lines


# --------------------------------------------------------------------------- public API

def compose_briefing(
    memory=None,
    scheduler=None,
    graph=None,
    include_news: bool = True,
) -> str:
    """Composes the daily briefing markdown. Never raises for ingredient failures."""
    now = datetime.now()
    now_utc = datetime.now(timezone.utc)
    sections: List[str] = []
    counts: Dict[str, int] = {}

    # Overdue + Today tasks -------------------------------------------------
    n_today = 0
    try:
        overdue_lines, today_task_lines, n_overdue, n_tasks_today, failed = _task_sections(
            memory, now
        )
    except Exception as e:
        logger.warning("Briefing task ingredient unavailable: %s", e)
        overdue_lines, today_task_lines, n_overdue, n_tasks_today, failed = [], [], 0, 0, True
    n_today += n_tasks_today
    counts["overdue"] = n_overdue
    sec = _section("🔴 Overdue", overdue_lines, failed)
    if sec:
        sections.append(sec)

    # Today: tasks + calendar events ----------------------------------------
    events: List[Dict[str, Any]] = []
    cal_failed = False
    try:
        events = _calendar_events(graph, now_utc)
    except Exception as e:
        logger.warning("Briefing calendar ingredient unavailable: %s", e)
        cal_failed = True
    today_lines = list(today_task_lines)
    for ev in events:
        when = (
            f" at {ev['start_dt'].astimezone().strftime('%H:%M')}" if ev["start_dt"] else ""
        )
        where = f" — 📍 {ev['location']}" if ev["location"] else ""
        today_lines.append(f"- 📅 **{ev['title']}**{when}{where}")
    n_today += len(events)
    counts["today"] = n_today
    sec = _section("📅 Today", today_lines, failed and cal_failed)
    if sec:
        sections.append(sec)

    # Reminders ---------------------------------------------------------------
    try:
        reminder_lines = _reminder_lines(scheduler, now)
    except Exception as e:
        logger.warning("Briefing reminder ingredient unavailable: %s", e)
        reminder_lines = None  # type: ignore[assignment]
    counts["reminders"] = len(reminder_lines) if reminder_lines is not None else 0
    sec = _section("⏰ Reminders", reminder_lines or [], reminder_lines is None)
    if sec:
        sections.append(sec)

    # Priority Focus -----------------------------------------------------------
    try:
        priority_lines = _priority_lines(memory)
    except Exception as e:
        logger.warning("Briefing priority ingredient unavailable: %s", e)
        priority_lines = None  # type: ignore[assignment]
    sec = _section("🎯 Priority Focus", priority_lines or [], priority_lines is None)
    if sec:
        sections.append(sec)

    # Overnight Research -------------------------------------------------------
    if include_news:
        try:
            research_lines = _research_lines(scheduler, now)
        except Exception as e:
            logger.warning("Briefing research ingredient unavailable: %s", e)
            research_lines = None  # type: ignore[assignment]
        sec = _section("🌍 Overnight Research", research_lines or [], research_lines is None)
        if sec:
            sections.append(sec)

    counts_summary = (
        f"{counts.get('overdue', 0)} overdue · "
        f"{counts.get('today', 0)} today · "
        f"{counts.get('reminders', 0)} reminder"
        f"{'s' if counts.get('reminders', 0) != 1 else ''}"
    )
    header = (
        f"🌅 AJA Briefing — {now.strftime('%A, %B %d %Y')}\n\n"
        f"> {counts_summary}"
    )

    parts = [header] + [s for s in sections if s]
    return "\n\n".join(parts)


def send_briefing(
    memory=None,
    scheduler=None,
    graph=None,
    include_news: bool = True,
) -> str:
    """Composes the briefing and publishes it on the runtime event bus.

    Platform telemetry tails (Telegram/Discord) subscribe to
    ``EVENTS["MISSION_COMPLETED"]`` and deliver ``payload["message"]``.
    """
    from aja.runtime.event_bus import bus, EVENTS

    text = compose_briefing(
        memory=memory,
        scheduler=scheduler,
        graph=graph,
        include_news=include_news,
    )
    event_type = EVENTS.get("MISSION_COMPLETED", "MISSION_COMPLETED")
    bus.publish(event_type, {"message": text})
    return text


def register_briefing_jobs(
    scheduler,
    morning: str = "0 7 * * *",
    evening: str = "0 19 * * *",
) -> List[Dict[str, Any]]:
    """Registers the recurring morning/evening briefing jobs (idempotent).

    Jobs carry ``meta {"briefing": True, "slot": "morning"|"evening"}``; at fire
    time ``cron_scheduler._execute_job`` routes them to the in-process
    :func:`send_briefing` branch — no SwarmEngine is spawned.

    Returns the list of newly created jobs (empty when already registered).
    """
    desired = {"morning": morning, "evening": evening}
    registered_slots = {
        meta.get("slot")
        for _, meta in _load_jobs(scheduler)
        if meta.get("briefing")
    }
    created: List[Dict[str, Any]] = []
    for slot, expr in desired.items():
        if not expr or slot in registered_slots:
            continue
        created.append(scheduler.add_job(BRIEFING_GOAL, expr, briefing=True, slot=slot))
    return created
