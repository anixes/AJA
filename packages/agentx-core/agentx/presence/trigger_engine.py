import os
import json
from datetime import datetime, timezone, timedelta

from agentx.persistence.triggers import fetch_active_triggers, disable_trigger
from agentx.persistence.tasks import create_task
from agentx.memory.manager import MemoryManager, get_memory_manager

_manager = get_memory_manager()

try:
    from agentx.persistence.tracker import log_event
except ImportError:
    def log_event(event, payload):
        pass

try:
    from agentx.presence.notifier import send_notification
except ImportError:
    def send_notification(e, p):
        pass

def evaluate_triggers():
    triggers = fetch_active_triggers()
    now = datetime.now(timezone.utc)
    
    for t in triggers:
        trigger_id = t["id"]
        trigger_type = t["trigger_type"]
        cooldown = t["cooldown_seconds"]
        last_triggered = t["last_triggered_at"]
        condition_payload = json.loads(t["condition_payload"])
        action_payload = json.loads(t["action_payload"])
        
        log_event("TRIGGER_EVALUATED", {"trigger_id": trigger_id, "type": trigger_type})
        
        # Check cooldown
        if last_triggered:
            last_dt = datetime.fromisoformat(last_triggered)
            if now < last_dt + timedelta(seconds=cooldown):
                log_event("TRIGGER_COOLDOWN_ACTIVE", {"trigger_id": trigger_id})
                continue
                
        condition_met = False
        
        try:
            if trigger_type == "TIME":
                interval = condition_payload.get("interval_seconds", 300)
                if not last_triggered:
                    condition_met = True
                else:
                    last_dt = datetime.fromisoformat(last_triggered)
                    if now >= last_dt + timedelta(seconds=interval):
                        condition_met = True
                        
            elif trigger_type == "TASK_STATE":
                status = condition_payload.get("status")
                try:
                    tasks_table = _manager.get_table("core_tasks")
                    last_seen_iso = last_triggered if last_triggered else (now - timedelta(days=365)).isoformat()
                    matching = tasks_table.search().where(
                        f"status = '{status}' AND updated_at > '{last_seen_iso}'"
                    ).to_list()
                    if matching:
                        condition_met = True
                except Exception as e:
                    print(f"[TriggerEngine] TASK_STATE check error: {e}")
                        
            elif trigger_type == "FILE_FLAG":
                path = condition_payload.get("path")
                if path and os.path.exists(path):
                    try:
                        proc_path = path + f".{trigger_id}.processing"
                        os.rename(path, proc_path)
                        condition_met = True
                        os.remove(proc_path)
                    except OSError:
                        # Another process or thread handled it first
                        pass
                    
        except Exception as e:
            print(f"[TriggerEngine] Error evaluating trigger {trigger_id}: {e}")
            continue
            
        if condition_met:
            # 1. Trigger duplication vs loop guardrails
            # Check if a recent task with same signature was enqueued
            action_str = json.dumps(action_payload)
            dup_cutoff = (now - timedelta(minutes=5)).isoformat()
            recent_task_exists = False
            
            try:
                tasks_table = _manager.get_table("core_tasks")
                recent_rows = tasks_table.search().where(
                    f"input = '{action_str}' AND created_at >= '{dup_cutoff}'"
                ).limit(1).to_list()
                if recent_rows:
                    recent_task_exists = True
            except Exception as e:
                print(f"[TriggerEngine] Dedupe check error: {e}")
                
            if recent_task_exists:
                log_event("TRIGGER_SKIPPED_DUPLICATE", {"trigger_id": trigger_id})
                update_trigger_time(trigger_id, now.isoformat())
                continue

            # Enqueue task
            try:
                task_id = create_task(action_payload)
                # Update trigger timestamp in Arrow table
                try:
                    triggers_table = _manager.get_table("core_triggers")
                    triggers_table.update(
                        where=f"trigger_id = '{trigger_id}'",
                        values={"created_at": now.isoformat()}  # reusing created_at as last_fired
                    )
                except Exception:
                    pass
                log_event("TRIGGER_FIRED", {"trigger_id": trigger_id, "task_id": task_id})
                send_notification("TRIGGER_FIRED", {"trigger_id": trigger_id, "task_id": task_id})
            except Exception as e:
                print(f"[TriggerEngine] Failed to fire trigger {trigger_id}: {e}")

        else:
            log_event("TRIGGER_SKIPPED", {"trigger_id": trigger_id})
