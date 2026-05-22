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

from agentx.orchestration.gateway import LLMGateway
from agentx.orchestration.registry import WorkerRegistry
from agentx.orchestration.verification_engine import run_verification
from agentx.utils.health_check import get_resource_telemetry

try:
    import agentx_native
except ImportError:
    print("Warning: agentx_native not found. Fallback not implemented since strict Rust native is required by SPEC.", file=sys.stderr)
    agentx_native = None

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
    if agentx_native:
        agentx_native.write_baton_ipc(str(path), json.dumps(baton_data))
    else:
        path.write_text(json.dumps(baton_data, indent=2))

def read_baton(path: Path):
    if agentx_native and str(path).endswith(".arrow"):
        return json.loads(agentx_native.read_baton_ipc(str(path)))
    else:
        return json.loads(path.read_text(encoding="utf-8"))

class SwarmEngine:
    """
    Unified Swarm Engine for AgentX.
    Orchestrates workers, manages batons, and enforces Phase 6 verification logic.
    """
    def __init__(self, provider: str = "nvidia", key: str = "dummy", model: str = "llama-3", dry_run: bool = False):
        import os
        from agentx.config import AGENTX_PLANNER_MODEL
        
        self.provider = provider if provider != "nvidia" else os.getenv("AI_PROVIDER", "google")
        self.api_key = key if key != "dummy" else (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "dummy")
        self.model = model if model != "llama-3" else (AGENTX_PLANNER_MODEL or "google:gemini-2.0-flash")
        self.dry_run = dry_run

        from agentx.llm import get_gateway_for_model
        model_query = f"{self.provider}:{self.model}" if ":" not in self.model else self.model
        self.gateway, self.model = get_gateway_for_model(model_query)
        self.provider = self.gateway.provider
        self.workers = {}
        self.registry = WorkerRegistry()
        # Using the unified BatonManager location
        self.baton_dir = PROJECT_ROOT / ".agentx" / "batons"
        self.baton_dir.mkdir(parents=True, exist_ok=True)
        
    async def execute_direct(self, objective: str):
        """
        Direct Tooling and In-Process Execution (Interactive Pairing Assistant).
        Executes commands synchronously in-process using ToolExecutor.
        Bypasses planning graphs, Arrow batons, and subprocesses entirely.
        """
        from agentx.interface.modern import console
        console.print(f"\n[bold cyan]🔧 [Direct Mode] Starting In-Process Execution for:[/] [italic]{objective}[/]")
        
        # 1. Initialize Direct Tool Executor
        from agentx.orchestration.tools.executor import ToolExecutor
        executor = ToolExecutor()
        
        # 2. Build AJA Assistant Prompt
        system_prompt = (
            "You are AJA (Assistant of Joint Agents), an elite hacker-butler, personal secretary, "
            "and operator operating directly in-process on the user's terminal.\n"
            "You have direct execution access to local filesystem and shell commands.\n"
            "Your objective is to accomplish the user's task using direct tooling execution.\n\n"
            "CONVERSATIONAL PERSONA:\n"
            "- Speak like a premium hacker-butler. Be extremely polite, refined, loyal, wittingly concise, "
            "and speak with absolute developer fluency (use terms like 'Sir', 'My friend', 'Operator').\n\n"
            "INSTRUCTIONS:\n"
            "1. Output your thought process and suggest standard shell/terminal commands inside ```bash or ```sh blocks to run next.\n"
            "2. If you suggest a command, it will be executed immediately, and the results (stdout, stderr) will be fed back to you.\n"
            "3. If you have completed the task or no further commands are needed, write your final response/synthesis and do not output any more commands.\n"
            "4. NEVER output raw forbidden words or reference deprecated components."
        )

        history = [
            {"role": "user", "content": f"Please execute this task directly: {objective}"}
        ]

        iteration = 0
        max_iterations = 10 # Prevent runaway loops
        
        while iteration < max_iterations:
            iteration += 1
            
            # Request LLM response
            try:
                response = await self.gateway.chat(
                    model=self.model,
                    prompt=history,
                    system=system_prompt
                )
            except Exception as e:
                console.print(f"[red][Direct Mode] LLM Chat Error: {e}[/red]")
                if self.dry_run:
                    response = "I have simulated the direct task completion successfully, Sir."
                else:
                    raise e
            
            if not response:
                console.print("[yellow][Direct Mode] Empty response from assistant. Exiting.[/yellow]")
                break

            # Print AJA's thought process/message
            console.print(f"\n[bold cyan]AJA:[/] {response.strip()}")
            
            # Record LLM's response to history
            history.append({"role": "assistant", "content": response})

            # Check for bash/sh command blocks
            commands = []
            if "```bash" in response:
                parts = response.split("```bash")
                for part in parts[1:]:
                    cmd = part.split("```")[0].strip()
                    if cmd:
                        commands.append(cmd)
            elif "```sh" in response:
                parts = response.split("```sh")
                for part in parts[1:]:
                    cmd = part.split("```")[0].strip()
                    if cmd:
                        commands.append(cmd)
            
            if not commands:
                # No more commands suggested, AJA has finished!
                console.print(f"\n[bold green][+] Direct In-Process task completed successfully.[/bold green]")
                break
            
            # Execute each suggested command
            all_completed_successfully = True
            for cmd in commands:
                console.print(f"\n[bold cyan][*] [Direct Execution] Running command:[/] [yellow]{cmd}[/]")
                
                # Check dry-run
                if self.dry_run:
                    from agentx.security.command_guard import classify_command
                    classification = classify_command(cmd)
                    console.print(f"[bold yellow][DRY-RUN AUDIT][/bold yellow] Command: '{cmd}' | Safety: {classification['decision'].upper()} (Risk: {classification['risk_level']})")
                    sim_stdout = f"[DRY-RUN SIMULATION OUTPUT] Successfully simulated command: {cmd}"
                    result = {
                        "status": "success",
                        "stdout": sim_stdout,
                        "stderr": "",
                        "code": 0
                    }
                else:
                    # Execute in-process
                    result = executor.execute(cmd)
                
                # Format output message
                if result.get("status") == "success":
                    console.print(f"[bold green]✔ Command succeeded with code {result.get('code', 0)}[/bold green]")
                    if result.get("stdout"):
                        console.print(f"[dim]{result['stdout']}[/dim]")
                else:
                    console.print(f"[bold red]✘ Command failed: {result.get('message', 'Unknown failure') or result.get('stderr')}[/bold red]")
                    all_completed_successfully = False
                
                # Feed result back into the history
                result_str = (
                    f"Command executed: {cmd}\n"
                    f"Status: {result.get('status')}\n"
                    f"Exit Code: {result.get('code', -1)}\n"
                    f"Stdout:\n{result.get('stdout', '')}\n"
                    f"Stderr:\n{result.get('stderr', '') or result.get('message', '')}"
                )
                history.append({"role": "user", "content": result_str})

            if not all_completed_successfully:
                pass
        
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

    def launch_parallel_swarm(self, objective: str, sub_tasks: list, providers: list):
        print(f"[*] Launching Parallel Swarm with {len(sub_tasks)} agents...")
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

    # --- MODE 3: BATON ORCHESTRATOR (Autonomous Tool Loop - Power 2 & 4) ---
    async def plan_and_execute_batons(self, objective: str, run_id: str = None, worker_id: str = "swarm-maintenance"):
        print(f"🐝 Orchestrating Autonomous Objective: {objective}")
        
        # ── Power 4: Deep Territory RAG ──
        try:
            from agentx.memory.secretary import get_aja_memory
            mem = get_aja_memory()
            # Generate dummy query vector (should be real if we had a local embedder)
            query_vec = [0.0] * 384 
            # In a real run, we'd use self.gateway.embed(objective)
            knowledge = mem.query_territory(query_vec, limit=5)
            rag_context = "\n".join([f"File: {k['path']}\nContent: {k['content']}" for k in knowledge])
        except Exception as e:
            print(f"RAG Lookup failed: {e}")
            rag_context = "No additional codebase context available."

        # ── Power 5: Hot-Swapping Skills (Synthetic Library) ──
        try:
            from agentx.skills.skill_store import SkillStore
            sk_store = SkillStore()
            relevant_skills = sk_store.search_skills(objective, limit=3)
            skills_context = "\n".join([f"Skill: {s['name']}\nDescription: {s['description']}\nTools: {s['tool_sequence_json']}" for s in relevant_skills])
        except Exception as e:
            print(f"Skill search failed: {e}")
            skills_context = "No relevant synthetic skills found."

        planning_prompt = (
            f"Objective: '{objective}'\n\n"
            f"CODEBASE CONTEXT (RAG):\n{rag_context}\n\n"
            f"AVAILABLE SKILLS:\n{skills_context}\n\n"
            "Plan the steps to achieve this. You can suggest shell commands in ```bash blocks. "
            "Break it into 2-3 independent sub-tasks if needed. "
            "Return a JSON list with 'id', 'task', and 'suggested_commands'."
        )
        
        try:
            plan_str = await self.gateway.chat(model=self.model, prompt=planning_prompt)
        except Exception as e:
            if self.dry_run:
                print(f"[DRY-RUN] LLM Planning failed or is unauthenticated ({e}). Simulating a default safe plan.")
                plan_str = json.dumps([
                    {"id": 1, "task": f"Mock analysis: {objective}", "suggested_commands": ["grep -rn \"TODO\" ."]}
                ])
            else:
                raise e

        if not plan_str:
            if self.dry_run:
                print("[DRY-RUN] LLM returned empty plan. Simulating a default safe plan.")
                plan_str = json.dumps([
                    {"id": 1, "task": f"Mock analysis: {objective}", "suggested_commands": ["grep -rn \"TODO\" ."]}
                ])
            else:
                plan_str = ""
        
        # ── Power 2: Autonomous Tool Loop ──
        if self.dry_run:
            print("[DRY-RUN] Simulating tool planning and verification. No physical system changes will be made.")
            commands = []
            if "```bash" in plan_str:
                parts = plan_str.split("```bash")
                for part in parts[1:]:
                    cmd = part.split("```")[0].strip()
                    if cmd:
                        commands.append(cmd)
            elif "```sh" in plan_str:
                parts = plan_str.split("```sh")
                for part in parts[1:]:
                    cmd = part.split("```")[0].strip()
                    if cmd:
                        commands.append(cmd)
            for cmd in commands:
                from agentx.security.command_guard import classify_command
                classification = classify_command(cmd)
                print(f"[DRY-RUN SIMULATION] Auditing command: '{cmd}' | Safety: {classification['decision'].upper()} (Risk: {classification['risk_level']})")
            tool_results = []
        else:
            from agentx.orchestration.tools.executor import ToolExecutor
            executor = ToolExecutor()
            # Parse suggested commands from the planning stage
            tool_results = executor.parse_and_run(plan_str)
            if tool_results:
                print(f"🔧 Executed {len(tool_results)} autonomous prep-tools.")

        try:
            plan_str = plan_str.strip().replace("```json", "").replace("```", "")
            # Find the JSON part if there was extra text
            start = plan_str.find("[")
            end = plan_str.rfind("]") + 1
            if start != -1 and end != -1:
                plan = json.loads(plan_str[start:end])
            else:
                plan = []
        except Exception:
            print("Planning failed. Defaulting to single-step execution.")
            plan = [{"id": 1, "task": objective}]

        results = []
        for task in plan:
            task_worker_id = f"worker-{task['id']}"
            print(f"  - Dispatching Worker {task_worker_id}: {task['task']}")
            baton_id = f"baton_{task_worker_id}_{int(time.time())}.arrow"
            baton_path = self.baton_dir / baton_id
            baton_data = {
                "objective": task['task'],
                "status": "pending",
                "stage": "init",
                "delegated_worker": task_worker_id
            }
            write_baton(baton_path, baton_data)
            
            result_data = await self._execute_baton_worker(baton_path)
            results.append({"id": task['id'], "status": result_data.get("status"), "baton_path": str(baton_path)})

        results_str = json.dumps(results, indent=2)
        
        # MEMORY CHECK: If the results are too large, summarize them first to stay under the 'Latency Wall'
        if len(results_str) > 5000: # Aggressive 5k limit for 4GB VRAM stability
            print("[MEMORY] Context threshold reached. Summarizing task history to maintain reasoning speed...")
            results_str = await self.gateway.chat(model=self.model, prompt=f"Summarize these task results concisely: {results_str}")
            
        synthesis_prompt = f"Objective: {objective}\nSub-task results: {results_str}\nSynthesize these results into a final report."
        final_report = await self.gateway.chat(model=self.model, prompt=synthesis_prompt)

        print("\nFinal Synthesis Complete:\n" + str(final_report))

    async def _execute_baton_worker(self, baton_path: Path):
        baton_data = read_baton(baton_path)
        baton_data["status"] = "executing"
        baton_data["stage"] = "dispatching"
        append_baton_history(baton_data, "dispatching", f"Agent worker dispatched to {baton_path.name}")
        write_baton(baton_path, baton_data)

        if self.dry_run:
            print(f"  [DRY-RUN SIMULATION] Simulating worker execution for baton: '{baton_path.name}'")
            latency = 0.05
            baton_data = read_baton(baton_path)
            baton_data["worker_stdout"] = f"[DRY-RUN MOCK STDOUT] Swarm worker successfully processed objective: {baton_data['objective']}"
            baton_data["worker_stderr"] = ""
            baton_data["status"] = "completed"
            baton_data["stage"] = "done"
            append_baton_history(baton_data, "done", "[DRY-RUN] Simulated worker completed successfully.")
            telemetry = get_resource_telemetry()
            self.registry.update_metrics(baton_data.get("delegated_worker", "unknown"), True, latency, telemetry)
            write_baton(baton_path, baton_data)
            return baton_data

        start_time = time.time()
        process = subprocess.run(
            [PYTHON, "-m", "agentx.agents.worker", str(baton_path)],
            capture_output=True, text=True
        )
        latency = time.time() - start_time

        baton_data = read_baton(baton_path)
        baton_data["worker_stdout"] = process.stdout.strip()
        baton_data["worker_stderr"] = process.stderr.strip()

        if process.returncode != 0:
            baton_data["status"] = "failed"
            baton_data["stage"] = "dispatch_failed"
            baton_data["error"] = process.stderr.strip() or "Worker process non-zero exit code."
            append_baton_history(baton_data, "dispatch_failed", "Agent worker encountered a process error.")
            telemetry = get_resource_telemetry()
            self.registry.update_metrics(baton_data.get("delegated_worker", "unknown"), False, latency, telemetry)
            write_baton(baton_path, baton_data)
            return {"status": "failed", "error": process.stderr}

        # --- Phase 6: Verification Hook ---
        baton_data["stage"] = "verifying"
        verification = run_verification(baton_data, str(PROJECT_ROOT))
        baton_data["verification"] = verification

        telemetry = get_resource_telemetry()
        if not verification["passed"]:
            baton_data["status"] = "failed"
            baton_data["stage"] = "verification_failed"
            msg = f"Verification failed: {[c['message'] for c in verification['checks'] if not c['passed']]}"
            append_baton_history(baton_data, "verification_failed", msg)
            self.registry.update_metrics(baton_data.get("delegated_worker", "unknown"), False, latency, telemetry)
        else:
            baton_data["status"] = "completed"
            baton_data["stage"] = "done"
            append_baton_history(baton_data, "done", "Assistant accepted the worker result after verification.")
            self.registry.update_metrics(baton_data.get("delegated_worker", "unknown"), True, latency, telemetry)

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
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without real system changes")
    args = parser.parse_args()

    task_input = args.task or args.objective

    provider = os.getenv("AI_PROVIDER", "nvidia")
    key = os.getenv("AI_KEY", "dummy")
    model = os.getenv("AI_MODEL", "llama-3")

    engine = SwarmEngine(provider, key, model, dry_run=args.dry_run)

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
