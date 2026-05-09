import sqlite3
import os
import json
from datetime import datetime, timezone, timedelta

from agentx.persistence.tasks import DB_PATH
from agentx.persistence.tracker import TRACKER_DB

def get_system_state() -> dict:
    state = {
        "active_tasks": 0,
        "pending_tasks": 0,
        "failed_tasks": 0,
        "loop_status": "stopped",
        "recent_events": [],
        "trigger_count": 0,
        "last_loop_tick": None,
        "is_healthy": True,
        "load_level": "LOW",
        "stalled_tasks_exist": False,
        "circuit_breaker_triggered": False,
        "recent_failures": 0
    }

    try:
        # Check loop status
        if os.path.exists(".agentx/stop_loop"):
            state["loop_status"] = "stopped (flagged)"
            state["circuit_breaker_triggered"] = True

        with sqlite3.connect(DB_PATH) as conn:
            # Task counts
            row = conn.execute(
                "SELECT status, COUNT(*) FROM tasks GROUP BY status"
            ).fetchall()
            for r in row:
                if r[0] == "RUNNING":
                    state["active_tasks"] = r[1]
                elif r[0] == "PENDING":
                    state["pending_tasks"] = r[1]
                elif r[0] == "FAILED":
                    state["failed_tasks"] = r[1]
                elif r[0] == "FAILED_PERMANENT":
                    state["stalled_tasks_exist"] = True

            # Trigger count
            row = conn.execute("SELECT COUNT(*) FROM triggers WHERE is_active = 1").fetchone()
            if row:
                state["trigger_count"] = row[0]

        # Recent events and loop tick from tracker
        if os.path.exists(TRACKER_DB):
            with sqlite3.connect(TRACKER_DB) as conn:
                conn.row_factory = sqlite3.Row
                events = conn.execute(
                    "SELECT event_type, payload, timestamp FROM agent_events ORDER BY timestamp DESC LIMIT 20"
                ).fetchall()
                
                recent = []
                for e in events:
                    ev = dict(e)
                    recent.append(ev)
                    if ev["event_type"] == "AGENT_LOOP_TICK" and not state["last_loop_tick"]:
                        state["last_loop_tick"] = ev["timestamp"]
                    if ev["event_type"] == "CIRCUIT_BREAKER_TRIGGERED":
                        state["circuit_breaker_triggered"] = True
                    if ev["event_type"] == "TASK_FAILED":
                        state["recent_failures"] += 1
                
                state["recent_events"] = recent

        # Compute loop status based on last tick
        if state["loop_status"] != "stopped (flagged)" and state["last_loop_tick"]:
            last_tick_dt = datetime.fromisoformat(state["last_loop_tick"])
            if datetime.now(timezone.utc) - last_tick_dt < timedelta(minutes=2):
                state["loop_status"] = "running"
            else:
                state["loop_status"] = "stopped (timeout)"

        # Compute Health
        if state["circuit_breaker_triggered"] or state["recent_failures"] >= 5 or state["stalled_tasks_exist"]:
            state["is_healthy"] = False

        # Compute Load
        pt = state["pending_tasks"]
        if pt > 20:
            state["load_level"] = "HIGH"
        elif pt > 5:
            state["load_level"] = "MEDIUM"
        else:
            state["load_level"] = "LOW"

    except Exception as e:
        print(f"[State] Error retrieving system state: {e}")
        state["is_healthy"] = False

    return state
