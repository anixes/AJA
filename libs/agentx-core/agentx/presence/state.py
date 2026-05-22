import os
import pyarrow as pa
import pyarrow.compute as pc
from datetime import datetime, timezone, timedelta
from agentx.memory.manager import MemoryManager, get_memory_manager

_manager = get_memory_manager()


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
        "recent_failures": 0,
    }

    try:
        if os.path.exists(".agentx/stop_loop"):
            state["loop_status"] = "stopped (flagged)"
            state["circuit_breaker_triggered"] = True

        # ── Task counts via Arrow compute — zero Python-level iteration (PERF-02) ──
        tasks_table = _manager.get_table("core_tasks")
        arrow = tasks_table.to_arrow()
        if len(arrow) > 0:
            status_col = arrow["status"]
            state["active_tasks"] = pc.sum(pc.cast(pc.equal(status_col, "RUNNING"), pa.int64())).as_py() or 0
            state["pending_tasks"] = (
                pc.sum(pc.cast(pc.equal(status_col, "PENDING"), pa.int64())).as_py() or 0
            )
            state["failed_tasks"] = pc.sum(pc.cast(pc.equal(status_col, "FAILED"), pa.int64())).as_py() or 0
            stalled = pc.sum(pc.cast(pc.equal(status_col, "FAILED_PERMANENT"), pa.int64())).as_py() or 0
            state["stalled_tasks_exist"] = stalled > 0

        # ── Trigger count ────────────────────────────────────────────────────
        try:
            triggers_table = _manager.get_table("core_triggers")
            active_triggers = (
                triggers_table.search().where("status = 'ACTIVE'").to_list()
            )
            state["trigger_count"] = len(active_triggers)
        except Exception:
            pass

        # ── Recent events from Arrow event feed ──────────────────────────────
        try:
            events_table = _manager.get_table("agentx_runtime_events")
            events = events_table.to_arrow().to_pylist()
            events.sort(key=lambda e: e.get("created_at", ""), reverse=True)
            recent = events[:20]
            for ev in recent:
                etype = ev.get("event_type", "")
                if etype == "AGENT_LOOP_TICK" and not state["last_loop_tick"]:
                    state["last_loop_tick"] = ev.get("created_at")
                if etype == "CIRCUIT_BREAKER_TRIGGERED":
                    state["circuit_breaker_triggered"] = True
                if etype == "TASK_FAILED":
                    state["recent_failures"] += 1
            state["recent_events"] = recent
        except Exception:
            pass

        # ── Loop status from last tick timestamp ─────────────────────────────
        if state["loop_status"] != "stopped (flagged)" and state["last_loop_tick"]:
            last_tick_dt = datetime.fromisoformat(state["last_loop_tick"])
            if datetime.now(timezone.utc) - last_tick_dt < timedelta(minutes=2):
                state["loop_status"] = "running"
            else:
                state["loop_status"] = "stopped (timeout)"

        # ── Health & Load ─────────────────────────────────────────────────────
        if (
            state["circuit_breaker_triggered"]
            or state["recent_failures"] >= 5
            or state["stalled_tasks_exist"]
        ):
            state["is_healthy"] = False

        pt = state["pending_tasks"]
        state["load_level"] = "HIGH" if pt > 20 else "MEDIUM" if pt > 5 else "LOW"

    except Exception as e:
        print(f"[State] Error retrieving system state: {e}")
        state["is_healthy"] = False

    return state
