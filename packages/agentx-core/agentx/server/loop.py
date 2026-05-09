import asyncio
from agentx.server.api import task_queue
from agentx.runtime.session import Session

def execute_task_sync(session: Session, task: str):
    """
    Synchronous bridge to the AgentX Planner and Executor.
    In production, this invokes Planner.decompose() and ReActExecutor.run()
    """
    print(f"[Engine] Starting execution for '{task}'...")
    from agentx.planning.react_executor import ReActExecutor
    from agentx.planning.models import PlanGraph
    
    # Normally planner.decompose(task) would happen here
    # We pass the session to ReActExecutor to enable interruptions
    # executor = ReActExecutor(graph, session=session)
    # executor.run()

    
    from agentx.runtime.event_bus import bus, EVENTS
    class MockNode:
        id = "n_1"
        tool = "terminal.exec"
    
    node = MockNode()
    bus.publish(EVENTS["NODE_STARTED"], node)
    time.sleep(1)
    bus.publish(EVENTS["NODE_SUCCESS"], node)
    
    print(f"[Engine] Execution completed.")

async def jarvis_loop():
    """Background worker that pulls tasks and executes them seamlessly."""
    print("[Jarvis] Event Loop started. Waiting for tasks...")
    
    while True:
        task_data = await task_queue.get()
        session: Session = task_data["session"]
        task: str = task_data["task"]
        
        print(f"[Jarvis] Processing task for user {session.user_id}: {task}")
        
        try:
            # ReActExecutor is synchronous currently, run in thread to keep API responsive
            await asyncio.to_thread(execute_task_sync, session, task)
            session.log_interaction("agent", f"Completed: {task}")
        except Exception as e:
            print(f"[Jarvis] Task failed: {e}")
            session.log_interaction("agent", f"Failed: {e}")
            
        task_queue.task_done()
