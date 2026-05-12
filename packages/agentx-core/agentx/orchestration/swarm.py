import os
import json
import asyncio
import sys
import time
import subprocess
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone
from agentx.config import PROJECT_ROOT

# Local imports - ensuring we use the optimized local gateway
try:
    from agentx.gateway import UnifiedGateway
except ImportError:
    from agentx.orchestration.gateway import UnifiedGateway

PYTHON = sys.executable

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_baton_history(baton_data, stage, message):
    baton_data.setdefault("history", []).append({
        "stage": stage,
        "message": message,
        "timestamp": now_iso(),
    })
    baton_data["updated_at"] = now_iso()

def write_baton(path: Path, baton_data):
    path.write_text(json.dumps(baton_data, indent=2))

class SwarmEngine:
    """
    Unified Swarm Engine replacing BatonOrchestrator, SwarmController, and SwarmLauncher.
    """
    def __init__(self, provider: str = "nvidia", key: str = "dummy", model: str = "llama-3"):
        self.gateway = UnifiedGateway(model_id=model)
        self.model = model
        self.provider = provider
        self.workers = {}
        # Using the unified BatonManager location
        self.baton_dir = PROJECT_ROOT / ".agentx" / "batons"
        self.baton_dir.mkdir(parents=True, exist_ok=True)
        
    # --- MODE 1: BACKGROUND TERRITORY MONITORING (Swarm Controller) ---
    def load_config(self):
        config_path = PROJECT_ROOT / "agentx.json"
        if not config_path.exists():
            return {"territories": []}
        with open(config_path, "r") as f:
            return json.load(f)

    def deploy_background_swarm(self):
        print("--- AGENTX BACKGROUND SWARM DEPLOYMENT ---")
        config = self.load_config()
        territories = config.get("territories", [])
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()

        for entry in territories:
            territory = entry["path"]
            if os.path.exists(territory):
                print(f"[-] Dispatching Healing Worker to territory: {territory}")
                process = subprocess.Popen(
                    [PYTHON, "-m", "agentx.utils.self_healer", territory],
                    env=env
                )
                self.workers[territory] = process
        
        print(f"\n[+] Swarm Active: {len(self.workers)} agents monitoring the system.")
        print("Press Ctrl+C to recall the swarm.")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n[!] Recalling the swarm. Terminating all agents...")
            for territory, process in self.workers.items():
                process.terminate()
            print("[+] Swarm offline.")

    # --- MODE 2: PARALLEL TASK LAUNCHER (Swarm Launcher) ---
    def _run_agent_sync(self, agent_id: int, task: str, target_provider: str):
        print(f"🐝 [Agent {agent_id}] Starting task on {target_provider.upper()}...")
        cmd = [
            PYTHON, "-m", "agentx.orchestration.gateway",
            "--provider", target_provider,
            "--key", self.gateway.api_key,
            "--model", self.model,
            "--prompt", task
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return {"agent_id": agent_id, "provider": target_provider, "status": "success", "output": result.stdout.strip()}
        except subprocess.CalledProcessError as e:
            return {"agent_id": agent_id, "provider": target_provider, "status": "failed", "error": e.stderr}

        print(f"🚀 Launching Parallel Swarm with {len(sub_tasks)} agents...")
        results = []
        # Cap workers at CPU count to prevent resource exhaustion (PERF-04)
        max_w = min(len(sub_tasks), os.cpu_count() or 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_w) as executor:
            future_to_agent = {
                executor.submit(
                    self._run_agent_sync, i, sub_tasks[i], providers[i % len(providers)]
                ): i for i in range(len(sub_tasks))
            }
            for future in concurrent.futures.as_completed(future_to_agent):
                results.append(future.result())
        return results

    # --- MODE 3: BATON ORCHESTRATOR ---
    async def plan_and_execute_batons(self, objective: str, run_id: str = None, worker_id: str = "swarm-maintenance"):
        print(f"Orchestrating Objective: {objective}")
        if objective.startswith("test:"):
            plan = [
                {"id": 1, "task": objective, "delegated_worker": "test-worker"}
            ]
        else:
            planning_prompt = (
                f"Break down this objective into 2-3 independent sub-tasks: '{objective}'. "
                "Return ONLY a JSON list of objects with 'id', 'task', and 'file_context'."
            )
            plan_str = await self.gateway.chat(planning_prompt)
            try:
                plan_str = plan_str.strip().replace("```json", "").replace("```", "")
                plan = json.loads(plan_str)
            except Exception:
                print("Planning failed. AI did not return valid JSON.")
                return

        results = []
        for task in plan:
            print(f"  - Dispatching Worker for Task {task['id']}: {task['task']}")
            # Use the high-performance Arrow-backed spawn mechanism
            code = await self.gateway.spawn_sub_agent(f"worker-{task['id']}", task['task'])
            results.append({"id": task['id'], "status": "dispatched", "baton_code": code})

        results_str = json.dumps(results, indent=2)
        
        # MEMORY CHECK: If the results are too large, summarize them first to stay under the 'Latency Wall'
        if len(results_str) > 5000: # Aggressive 5k limit for 4GB VRAM stability
            print("[MEMORY] Context threshold reached. Summarizing task history to maintain reasoning speed...")
            results_str = await self.gateway.summarize(results_str, objective=objective)
            
        synthesis_prompt = f"Objective: {objective}\nSub-task results: {results_str}\nSynthesize these results into a final report."
        final_report = await self.gateway.chat(synthesis_prompt)

        print("\nFinal Synthesis Complete:\n" + final_report)

    async def _execute_baton_worker(self, baton_path: Path):
        baton_data = json.loads(baton_path.read_text())
        baton_data["status"] = "executing"
        baton_data["stage"] = "dispatching"
        append_baton_history(baton_data, "dispatching", "Worker process launched.")
        write_baton(baton_path, baton_data)

        process = subprocess.run(
            [PYTHON, "-m", "agentx.agents.worker", str(baton_path)],
            capture_output=True, text=True
        )

        baton_data = json.loads(baton_path.read_text())
        baton_data["worker_stdout"] = process.stdout.strip()
        baton_data["worker_stderr"] = process.stderr.strip()

        if process.returncode != 0:
            baton_data["status"] = "failed"
            baton_data["stage"] = "dispatch_failed"
            baton_data["error"] = process.stderr.strip() or "Worker process non-zero exit code."
            append_baton_history(baton_data, "dispatch_failed", "Worker process exited with an error.")
            write_baton(baton_path, baton_data)
            return {"status": "failed", "error": process.stderr}

        baton_data["stage"] = "verifying"
        if baton_data.get("status") == "completed":
            baton_data["stage"] = "done"
            append_baton_history(baton_data, "done", "Orchestrator accepted the worker result.")
        elif baton_data.get("status") != "failed":
            baton_data["status"] = "completed"
            baton_data["stage"] = "done"
            append_baton_history(baton_data, "done", "Orchestrator marked completed.")

        write_baton(baton_path, baton_data)
        return baton_data

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Unified Swarm Engine")
    parser.add_argument("--mode", choices=["background", "parallel", "baton"], required=True)
    parser.add_argument("--task", type=str, help="Main task description (for parallel/baton)")
    parser.add_argument("--objective", type=str, help="Alias for task")
    parser.add_argument("--items", type=str, help="Comma-separated items (for parallel)")
    parser.add_argument("--providers", type=str, default="nvidia,groq", help="Comma-separated providers (for parallel)")
    parser.add_argument("--worker", type=str, help="Assigned worker ID", default="swarm-maintenance")
    parser.add_argument("--run-id", type=str, help="Run ID for idempotency")
    args = parser.parse_args()

    task_input = args.task or args.objective

    provider = os.getenv("AI_PROVIDER", "nvidia")
    key = os.getenv("AI_KEY", "dummy")
    model = os.getenv("AI_MODEL", "llama-3")

    engine = SwarmEngine(provider, key, model)

    if args.mode == "background":
        engine.deploy_background_swarm()
    elif args.mode == "parallel":
        if not task_input or not args.items:
            print("Error: --task and --items required for parallel mode.")
            sys.exit(1)
        items = args.items.split(",")
        providers = args.providers.split(",")
        sub_tasks = [f"{task_input} for item: {item}" for item in items]
        engine.launch_parallel_swarm(task_input, sub_tasks, providers)
    elif args.mode == "baton":
        if not task_input:
            print("Error: --task or --objective required for baton mode.")
            sys.exit(1)
        asyncio.run(engine.plan_and_execute_batons(task_input, run_id=args.run_id, worker_id=args.worker))
