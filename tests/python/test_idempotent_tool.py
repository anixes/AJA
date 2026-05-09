import sys
import os
import time
import json
from agentx.persistence.tools import ToolGuard, PermanentError, RetryableError

def main():
    if len(sys.argv) < 3:
        print("Usage: python test_idempotent_tool.py <run_id> <action> [args_json]")
        sys.exit(1)

    run_id = sys.argv[1]
    action = sys.argv[2]
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}

    # Initialize ToolGuard
    guard = ToolGuard(run_id=run_id, tool_name="test_tool", args={"action": action, **args})
    
    # Step 1: Reserve
    cached = guard.reserve()
    if cached is not None:
        print(f"COALESCE: {cached['result']}")
        return

    try:
        # Simulate work
        print(f"EXECUTING: {action}")
        
        if action == "crash":
            print("CRASHING NOW...")
            os._exit(1)
        
        if action == "fail_retryable":
            raise RetryableError("Simulated transient failure")
            
        if action == "fail_permanent":
            raise PermanentError("Simulated permanent failure")
            
        if action == "wait":
            time.sleep(5)
            
        result = f"Success: {action}"
        
        # Step 2: Complete
        guard.complete(result)
        print(f"COMPLETED: {result}")
        
    except PermanentError as e:
        guard.fail(str(e), error_type="PERMANENT")
        print(f"FAILED_PERMANENT: {e}")
        sys.exit(1)
    except Exception as e:
        # Default to retryable for unknown errors in this test tool
        guard.fail(str(e), error_type="RETRYABLE")
        print(f"FAILED_RETRYABLE: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
