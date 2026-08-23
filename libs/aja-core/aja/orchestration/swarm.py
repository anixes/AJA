import os
import json
import asyncio
import logging
import sys
import time
import subprocess
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone
from aja.config import PROJECT_ROOT, DATA_DIR
from aja.utils.redact import redact_secrets

logger = logging.getLogger(__name__)

from aja.orchestration.gateway import LLMGateway
from aja.orchestration.registry import WorkerRegistry
from aja.orchestration.verification_engine import run_verification
from aja.runtime.handover import read_baton_ipc, write_baton_ipc
from aja.utils.health_check import get_resource_telemetry

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
    write_baton_ipc(path, baton_data)

def read_baton(path: Path):
    return read_baton_ipc(path)

class SwarmEngine:
    """
    Unified Swarm Engine for AJA.
    Orchestrates workers, manages batons, and enforces Phase 6 verification logic.
    """
    def __init__(self, provider: str = "nvidia", key: str = "dummy", model: str = "llama-3", dry_run: bool = False, presenter=None):
        import os
        from aja.config import AJA_PLANNER_MODEL
        
        model_resolved = model if model != "llama-3" else (AJA_PLANNER_MODEL or "google:gemini-2.0-flash")
        
        if ":" in model_resolved:
            parts = model_resolved.split(":", 1)
            self.provider = parts[0]
            self.model = parts[1]
        else:
            self.model = model_resolved
            if "gemini" in model_resolved.lower():
                self.provider = "google"
            elif "gemma" in model_resolved.lower() or "llama" in model_resolved.lower():
                self.provider = "llama_cpp"
            elif "copilot" in model_resolved.lower():
                self.provider = "copilot"
            else:
                self.provider = provider if provider != "nvidia" else os.getenv("AI_PROVIDER", "google")
                
        self.api_key = key if key != "dummy" else (os.getenv(f"{self.provider.upper()}_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "dummy")
        self.dry_run = dry_run

        from aja.llm import get_gateway_for_model
        model_query = f"{self.provider}:{self.model}" if ":" not in self.model else self.model
        self.gateway, self.model = get_gateway_for_model(model_query)
        self.provider = self.gateway.provider
        self.workers = {}
        self.registry = WorkerRegistry()
        if presenter is None:
            from aja.gateway.presenter import AJAPresenter
            presenter = AJAPresenter()
        self.presenter = presenter
        # Using the unified BatonManager location
        self.baton_dir = DATA_DIR / "batons"
        self.baton_dir.mkdir(parents=True, exist_ok=True)
        
    async def execute_direct(self, objective: str, session_history: list = None, interactive: bool = True):
        """
        Direct Tooling and In-Process Execution (Interactive Pairing Assistant).
        Executes commands synchronously in-process using ToolExecutor.
        Bypasses planning graphs, Arrow batons, and subprocesses entirely.
        """
        from aja.interface.modern import console
        console.print(f"\n[bold cyan]🔧 [Direct Mode] Starting In-Process Execution for:[/] [italic]{objective}[/]")
        
        # 1. Initialize Direct Tool Executor
        from aja.runtime.execution.activity import ActivityContext, set_activity_context
        from aja.runtime.execution.sequencer import TelemetryEmitter, EventSequencer
        from aja.config import PROJECT_ROOT, DATA_DIR
        
        direct_root = DATA_DIR / "executions" / "direct"
        sequencer = EventSequencer("direct")
        emitter = TelemetryEmitter(direct_root, sequencer)
        
        from aja.runtime.replay_guards import derive_run_id
        run_id = derive_run_id(objective, 0)
        ctx = ActivityContext(is_replay=False, emitter=emitter, run_id=run_id)
        set_activity_context(ctx)

        from aja.orchestration.tools.executor import ToolExecutor
        executor = ToolExecutor()
        
        from aja.orchestration.tools.native import NativeToolRegistry
        native_registry = NativeToolRegistry(engine=None)
        
        # 2. Build client-specific prompt through the presenter boundary.
        system_prompt = self.presenter.direct_system_prompt

        # When session_history is supplied (DirectSession multi-turn mode), we use
        # the caller-owned list so history accumulates across turns (enabling
        # provider-side prompt caching of the static system prefix).
        # In single-shot mode (session_history=None), we create a fresh list.
        if session_history is not None:
            # The caller already appended the new user message before calling us.
            # We use their list directly — mutation is visible to the caller.
            history = session_history
        else:
            # Legacy single-shot mode: fresh ephemeral history.
            history = [
                {"role": "user", "content": f"Please execute this task directly: {objective}"}
            ]

        iteration = 0
        max_iterations = 10  # Prevent runaway loops
        
        while iteration < max_iterations:
            iteration += 1

            # Compress history before sending to LLM to stay within token budget
            from aja.orchestration.context_window import compress_history
            compress_history(history, model=self.model, provider=self.provider)

            # Request LLM response
            try:
                response = await self.gateway.chat(
                    model=self.model,
                    prompt=history,
                    system=system_prompt,
                    tools=native_registry.get_schemas(interactive=interactive)
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
                
            if isinstance(response, dict):
                content = response.get("content", "")
                tool_calls = response.get("tool_calls", [])
            else:
                content = response
                tool_calls = []

            if content:
                self.presenter.assistant(content)
                history.append({"role": "assistant", "content": content})
            elif tool_calls:
                # No text content, but we have tool calls. Still need to append the assistant message so the API doesn't complain about role alternation.
                # Actually, some APIs require the tool_calls to be in the assistant message, but since we are maintaining simple string history for now:
                history.append({"role": "assistant", "content": f"[Invoking {len(tool_calls)} tool(s)]"})
            
            # Execute Native Tools if any
            tools_executed = False
            if tool_calls:
                tools_executed = True
                formatted_calls = []
                for tc in tool_calls:
                    t_name = tc.get("name")
                    t_args_str = tc.get("arguments", "{}")
                    try:
                        t_args = json.loads(t_args_str) if isinstance(t_args_str, str) else t_args_str
                    except Exception:
                        t_args = {}
                    formatted_calls.append({"tool": t_name, "args": t_args})
                
                console.print(f"[bold cyan]⚙ Calling {len(formatted_calls)} Tool(s)...[/]")
                from aja.observability.telemetry import get_trace_id
                results = await executor.dispatch_tool_calls(
                    tool_calls=formatted_calls,
                    trace_id=get_trace_id(),
                    dry_run=self.dry_run,
                )
                for r in results:
                    if r.success:
                        console.print(f"[bold green]✔ Tool '{r.tool}' succeeded[/bold green]")
                        if r.data:
                            from rich.markup import escape
                            console.print(f"[dim]{escape(redact_secrets(str(r.data)))}[/dim]")
                    else:
                        from rich.markup import escape
                        err_msg = r.error or getattr(r, "stderr", None) or r.data
                        console.print(f"[bold red]✘ Tool '{r.tool}' failed: {escape(redact_secrets(str(err_msg)))}[/bold red]")
                    
                    from aja.orchestration.context_window import truncate_tool_result, MAX_TOOL_RESULT_CHARS
                    raw_output = str(r.data or r.error or getattr(r, "stderr", "") or "")
                    safe_output = truncate_tool_result(raw_output, MAX_TOOL_RESULT_CHARS)
                    obs = f"Tool '{r.tool}' result:\n{safe_output}"
                    history.append({"role": "user", "content": obs})

            # Check for legacy bash/sh command blocks
            commands = []
            if "```bash" in content:
                parts = content.split("```bash")
                for part in parts[1:]:
                    cmd = part.split("```")[0].strip()
                    if cmd:
                        commands.append(cmd)
            elif "```sh" in content:
                parts = content.split("```sh")
                for part in parts[1:]:
                    cmd = part.split("```")[0].strip()
                    if cmd:
                        commands.append(cmd)
            
            if not commands and not tools_executed:
                # No more commands or tools suggested; direct execution has finished.
                console.print(f"\n[bold green][+] Direct In-Process task completed successfully.[/bold green]")
                break
            
            # Execute each suggested command
            all_completed_successfully = True
            for cmd in commands:
                self.presenter.command(cmd)
                
                # Check dry-run
                if self.dry_run:
                    from aja.security.command_guard import classify_command
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
                        console.print(f"[dim]{redact_secrets(result['stdout'])}[/dim]")
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
        config_path = PROJECT_ROOT / "aja.json"
        if not config_path.exists():
            return {"territories": []}
        with open(config_path, "r") as f:
            return json.load(f)

    def deploy_background_swarm(self):
        logger.info("--- AJA BACKGROUND SWARM DEPLOYMENT ---")
        config = self.load_config()
        territories = config.get("territories", [])
        env = os.environ.copy()
        env["PYTHONPATH"] = os.getcwd()

        for entry in territories:
            territory = entry["path"]
            if os.path.exists(territory):
                logger.info("Dispatching Healing Worker to territory: %s", territory)
                process = subprocess.Popen(
                    [PYTHON, "-m", "aja.utils.self_healer", territory],
                    env=env
                )
                self.workers[territory] = process

        logger.info("Swarm Active: %d agents monitoring the system.", len(self.workers))
        logger.info("Press Ctrl+C to recall the swarm.")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            logger.warning("Recalling the swarm. Terminating all agents...")
            for territory, process in self.workers.items():
                process.terminate()
            logger.info("Swarm offline.")

    # --- MODE 2: PARALLEL TASK LAUNCHER (Swarm Launcher) ---
    def _run_agent_sync(self, agent_id: int, task: str, target_provider: str):
        logger.info("Agent %d starting task on %s...", agent_id, target_provider.upper())
        cmd = [
            PYTHON, "-m", "aja.orchestration.gateway",
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
        logger.info("Launching Parallel Swarm with %d agents...", len(sub_tasks))
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
        logger.info("Orchestrating Autonomous Objective: %s", redact_secrets(objective))
        
        # ── Power 4: Deep Territory RAG ──
        try:
            from aja.memory.territory import get_text_embedding
            from aja.runtime.lance_stores import LanceRuntimeStore
            mem = LanceRuntimeStore()
            # Real semantic query vector (falls back to deterministic
            # placeholder when the embedder is unavailable/mocked).
            query_vec = get_text_embedding(objective)
            knowledge = mem.query_territory(query_vec, limit=5)
            rag_context = "\n".join([f"File: {k['path']}\nContent: {k['content']}" for k in knowledge]) or "No additional codebase context available."
        except Exception as e:
            logger.warning("RAG Lookup failed: %s", e)
            rag_context = "No additional codebase context available."

        # ── Power 5: Hot-Swapping Skills (Synthetic Library) ──
        try:
            from aja.skills.skill_store import SkillStore
            sk_store = SkillStore()
            relevant_skills = sk_store.search_skills(objective, limit=3)
            skills_context = "\n".join([f"Skill: {s['name']}\nDescription: {s['description']}\nTools: {s['tool_sequence_json']}" for s in relevant_skills])
        except Exception as e:
            logger.warning("Skill search failed: %s", e)
            skills_context = "No relevant synthetic skills found."

        planning_prompt = (
            f"Objective: '{objective}'\n\n"
            f"CODEBASE CONTEXT (RAG):\n{rag_context}\n\n"
            f"AVAILABLE SKILLS:\n{skills_context}\n\n"
            "Plan the steps to achieve this objective. Break it into 2-3 independent sub-tasks if needed. "
            "Return ONLY a JSON list with 'id' and 'task' keys for each sub-task."
        )
        
        try:
            plan_str = await self.gateway.chat(model=self.model, prompt=planning_prompt)
        except Exception as e:
            if self.dry_run:
                logger.warning("[DRY-RUN] LLM Planning failed or is unauthenticated (%s). Simulating a default safe plan.", e)
                plan_str = json.dumps([
                    {"id": 1, "task": f"Mock analysis: {objective}"}
                ])
            else:
                raise e

        if not plan_str:
            if self.dry_run:
                logger.info("[DRY-RUN] LLM returned empty plan. Simulating a default safe plan.")
                plan_str = json.dumps([
                    {"id": 1, "task": f"Mock analysis: {objective}"}
                ])
            else:
                plan_str = ""
        
        # ── Power 2: Autonomous Tool Loop ──
        if self.dry_run:
            logger.info("[DRY-RUN] Simulating tool planning and verification. No physical system changes will be made.")

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
            logger.warning("Planning failed. Defaulting to single-step execution.")
            plan = [{"id": 1, "task": objective}]

        import hashlib
        if not run_id:
            h = hashlib.sha256(objective.encode("utf-8")).hexdigest()[:16]
            run_id = f"run-{h}"

        results = []
        for task in plan:
            task_worker_id = f"worker-{task['id']}"
            logger.info("Dispatching Worker %s: %s", task_worker_id, redact_secrets(str(task['task'])))
            baton_id = f"baton_{task_worker_id}_{task['id']}.arrow"
            baton_path = self.baton_dir / baton_id
            
            from aja.runtime.replay_guards import derive_session_id, derive_trace_id
            session_id = derive_session_id(run_id, str(task['id']))
            trace_id = derive_trace_id(run_id)
            
            baton_data = {
                "id": str(task['id']),
                "task": task['task'],
                "objective": task['task'],
                "status": "pending",
                "stage": "init",
                "delegated_worker": task_worker_id,
                "run_id": run_id,
                "metadata": {
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "run_id": run_id,
                }
            }
            write_baton(baton_path, baton_data)
            
            result_data = await self._execute_baton_worker(baton_path)
            results.append({"id": task['id'], "status": result_data.get("status"), "baton_path": str(baton_path)})

        results_str = json.dumps(results, indent=2)
        
        # MEMORY CHECK: If the results are too large, summarize them first to stay under the 'Latency Wall'
        if len(results_str) > 5000: # Aggressive 5k limit for 4GB VRAM stability
            logger.info("Context threshold reached. Summarizing task history to maintain reasoning speed...")
            results_str = await self.gateway.chat(model=self.model, prompt=f"Summarize these task results concisely: {results_str}")
            
        synthesis_prompt = f"Objective: {objective}\nSub-task results: {results_str}\nSynthesize these results into a final report."
        final_report = await self.gateway.chat(model=self.model, prompt=synthesis_prompt)

        logger.info("Final Synthesis Complete:\n%s", redact_secrets(str(final_report)))

    async def _execute_baton_worker(self, baton_path: Path):
        baton_data = read_baton(baton_path)
        baton_data["status"] = "executing"
        baton_data["stage"] = "dispatching"
        append_baton_history(baton_data, "dispatching", f"Agent worker dispatched to {baton_path.name}")
        write_baton(baton_path, baton_data)

        if self.dry_run:
            logger.info("[DRY-RUN SIMULATION] Simulating worker execution for baton: '%s'", baton_path.name)
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
        from aja.runtime.execution import ExecutionRequest, get_default_execution_manager
        from aja.observability.telemetry import get_trace_id

        # Phase 2: Plumb session_id and trace_id to ensure worker receives correct ActivityContext
        metadata = baton_data.get("metadata", {})
        trace_id = metadata.get("trace_id") or get_trace_id()
        session_id = metadata.get("session_id")
        run_id = metadata.get("run_id") or baton_data.get("run_id")

        env = {}
        if trace_id:
            env["AJA_TRACE_ID"] = trace_id
        if session_id:
            env["AJA_EXECUTION_SESSION_ID"] = session_id
        if run_id:
            env["AJA_RUN_ID"] = run_id

        # Propagate credentials and configurations from parent environment
        for k, v in os.environ.items():
            if k.startswith("COPILOT_") or k.startswith("AJA_"):
                env.setdefault(k, v)

        worker_cmd = f'"{PYTHON}" -m aja.agents.worker "{baton_path}"'
        # Multi-turn LLM workers (planning + tool calls + web fetches) routinely
        # exceed 180s; default raised and env-overridable.
        worker_timeout = int(os.getenv("AJA_WORKER_TIMEOUT_S", "600"))
        process = await get_default_execution_manager().run(
            ExecutionRequest(
                command=worker_cmd,
                timeout=worker_timeout,
                workspace_mode="direct",
                env=env,
                metadata={
                    "legacy_api": "SwarmEngine._execute_baton_worker",
                    "session_id": session_id,
                },
            )
        )
        latency = time.time() - start_time

        baton_data = read_baton(baton_path)
        baton_data["worker_stdout"] = process.stdout.strip()
        baton_data["worker_stderr"] = process.stderr.strip()

        if process.exit_code != 0:
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
