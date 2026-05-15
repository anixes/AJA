import time
import threading

class ScheduledTask:
    def __init__(self, task_id: str, goal: str, interval: float, last_run: float = 0.0, paused: bool = False):
        self.id = task_id
        self.goal = goal
        self.interval = interval
        self.last_run = last_run
        self.paused = paused

class AutonomousScheduler:
    def __init__(self):
        self.tasks = []
        self._running = False
        self._thread = None
        
    def add_task(self, task_id: str, goal: str, interval: float):
        task = ScheduledTask(task_id, goal, interval)
        self.tasks.append(task)
        return task
        
    def pause_task(self, task_id: str):
        for task in self.tasks:
            if task.id == task_id:
                task.paused = True
                return True
        return False
        
    def resume_task(self, task_id: str):
        for task in self.tasks:
            if task.id == task_id:
                task.paused = False
                return True
        return False
        
    def get_tasks(self):
        return [{"id": t.id, "goal": t.goal, "interval": t.interval, "last_run": t.last_run, "paused": t.paused} for t in self.tasks]

    def _run_task(self, goal: str):
        # We need to run the planner and check risk/confidence
        from agentx.planning.planner import Planner
        from agentx.planning.verifier import verify_plan
        from agentx.decision.critic import critique_plan, critic_score
        
        planner = Planner()
        plan = planner.decompose(goal)
        
        # Part E - Safe Autonomy Rules
        # Estimate risk & confidence
        fb = verify_plan(plan)
        risk = fb.get("risk_score", 0.5)
        
        c_score = critic_score(plan, critique_plan(plan, {}))
        confidence = getattr(plan, "confidence", max(0.0, c_score * (1.0 - risk)))
        
        if risk > 0.7 or confidence < 0.6:
            print(f"[Scheduler] Task '{goal}' requires approval. Risk: {risk:.2f}, Confidence: {confidence:.2f}")
            # require_approval() -> normally send to Telegram or set status
            self._notify_telegram(f"Task '{goal}' requires approval. Risk: {risk:.2f}, Confidence: {confidence:.2f}")
            return False
            
        print(f"[Scheduler] Executing task autonomously: {goal}")
        # Normally execute here
        self._notify_telegram(f"Successfully executed task autonomously: {goal}")
        return True
        
    def _notify_telegram(self, message: str):
        # Mock Telegram integration
        print(f"[Telegram Bot] {message}")

    def loop(self):
        while self._running:
            now = time.time()
            for task in self.tasks:
                if not task.paused and (now - task.last_run > task.interval):
                    print(f"[Scheduler] Running scheduled task: {task.goal}")
                    success = self._run_task(task.goal)
                    if success:
                        task.last_run = time.time()
            time.sleep(5)
            
    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self.loop, daemon=True)
            self._thread.start()
            
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

scheduler = AutonomousScheduler()

# Mock Telegram command parser
def handle_telegram_command(command: str):
    parts = command.split(" ", 1)
    cmd = parts[0]
    args = parts[1] if len(parts) > 1 else ""
    
    if cmd == "/schedule":
        # e.g., /schedule "backup project every 6h"
        import uuid
        task_id = str(uuid.uuid4())[:8]
        # Extremely basic parsing for interval (mocked to 6h = 21600s)
        scheduler.add_task(task_id, args, 21600)
        return f"Scheduled task {task_id}."
    elif cmd == "/tasks":
        return str(scheduler.get_tasks())
    elif cmd == "/pause":
        success = scheduler.pause_task(args.strip())
        return f"Paused {args}" if success else "Task not found"
    elif cmd == "/resume":
        success = scheduler.resume_task(args.strip())
        return f"Resumed {args}" if success else "Task not found"
    return "Unknown command"
