import time
import os
import json

try:
    from agentx.persistence.tracker import log_event
except ImportError:
    def log_event(e, p): pass

try:
    from agentx.presence.notifier import send_notification
except ImportError:
    def send_notification(e, p): pass

APPROVAL_DIR = ".agentx/approvals"

def request_approval(task_id: int, objective: str, payload: dict = None) -> dict:
    """
    Pause execution and wait for human approval via file-based polling.
    """
    os.makedirs(APPROVAL_DIR, exist_ok=True)
    filepath = f"{APPROVAL_DIR}/{task_id}.json"
    
    data = {
        "status": "pending",
        "task_id": task_id,
        "objective": objective,
        "payload": payload or {}
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    log_event("APPROVAL_REQUESTED", {"task_id": task_id})
    # Send notification for high risk task
    send_notification("HIGH_RISK_TASK_STARTED", {"task_id": task_id, "objective": objective, "message": "Awaiting approval."})
    
    print(f"\n[!] HIGH RISK TASK PAUSED FOR APPROVAL.")
    print(f"Task ID: {task_id}")
    print(f"Objective: {objective}")
    print(f"To approve: agentx approve {task_id}")
    print(f"To reject:  agentx reject {task_id}")
    
    # --- Interactive Prompt (Phase 11) ---
    import sys
    if sys.stdin.isatty():
        try:
            choice = input("\nDo you want to [A]pprove, [R]eject, or [W]ait for external approval? (a/r/w): ").lower().strip()
            if choice == 'a':
                set_approval_status(task_id, "approved")
            elif choice == 'r':
                set_approval_status(task_id, "rejected")
        except EOFError:
            pass # Fallback to polling if input fails
    
    print("\n[*] Waiting for approval status update...\n")
    
    while True:
        if not os.path.exists(filepath):
            return {"status": "rejected", "modified_payload": payload}
            
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                current = json.load(f)
        except (json.JSONDecodeError, OSError):
            time.sleep(1)
            continue
            
        if current.get("status") == "approved":
            log_event("APPROVAL_GRANTED", {"task_id": task_id})
            os.remove(filepath)
            return {"status": "approved", "modified_payload": current.get("payload", payload)}
            
        elif current.get("status") == "rejected":
            log_event("APPROVAL_REJECTED", {"task_id": task_id})
            os.remove(filepath)
            return {"status": "rejected", "modified_payload": current.get("payload", payload)}
            
        time.sleep(2)

def set_approval_status(task_id: int, status: str, payload_path: str = None):
    """
    CLI interface to approve/reject a task.
    """
    filepath = f"{APPROVAL_DIR}/{task_id}.json"
    if not os.path.exists(filepath):
        print(f"Error: No pending approval for task {task_id}")
        return False
        
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    data["status"] = status
    
    if payload_path and os.path.exists(payload_path):
        with open(payload_path, "r", encoding="utf-8") as pf:
            data["payload"] = json.load(pf)
            
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    print(f"Task {task_id} successfully marked as {status}.")
    return True
