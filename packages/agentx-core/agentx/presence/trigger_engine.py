import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta

from agentx.persistence.triggers import fetch_active_triggers, update_trigger_time
from agentx.persistence.tasks import DB_PATH, create_task

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
                with sqlite3.connect(DB_PATH) as conn:
                    # only fire if new matching task appears AFTER last_seen
                    last_seen_iso = last_triggered if last_triggered else (now - timedelta(days=365)).isoformat()
                    row = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE status = ? AND updated_at > ?",
                        (status, last_seen_iso)
                    ).fetchone()
                    if row and row[0] > 0:
                        condition_met = True
                        
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
                with sqlite3.connect(DB_PATH) as conn:
                    row = conn.execute(
                        "SELECT id FROM tasks WHERE input = ? AND created_at >= ?",
                        (action_str, dup_cutoff)
                    ).fetchone()
                    if row:
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
                update_trigger_time(trigger_id, now.isoformat())
                log_event("TRIGGER_FIRED", {"trigger_id": trigger_id, "task_id": task_id})
                send_notification("TRIGGER_FIRED", {"trigger_id": trigger_id, "task_id": task_id})
            except Exception as e:
                print(f"[TriggerEngine] Failed to fire trigger {trigger_id}: {e}")
        else:
            log_event("TRIGGER_SKIPPED", {"trigger_id": trigger_id})
