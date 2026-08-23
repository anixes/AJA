import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

# Process-local cache of which worker models can honor output contracts.
# Prevents double-executing every task (contract attempt + plain fallback)
# on weak local models that raise StructuredOutputError every time.
_MODEL_CONTRACT_CAPABLE: Dict[str, bool] = {}


def _model_supports_contracts(model: str) -> bool:
    """False only when the model is known (this process) to fail output contracts."""
    return _MODEL_CONTRACT_CAPABLE.get(model, True)


def _mark_model_contract_capable(model: str, capable: bool) -> None:
    _MODEL_CONTRACT_CAPABLE[model] = capable


async def dispatch_worker(worker_id: str, baton: dict, workspace_dir: str) -> dict:
    """
    Dispatch a baton to the best available worker adapter.
    """
    if worker_id.startswith("worker-") or worker_id == "native-worker":
        adapter = NativeWorkerAdapter()
        return await adapter.run_async(baton, workspace_dir)

    adapters = {
        "github-copilot-cli": CopilotAdapter(),
        "gemini-cli": GeminiAdapter(),
        "aider-worker": AiderAdapter(),
        "codex-cli": CodexAdapter(),
        "swarm-maintenance": SwarmMaintenanceAdapter(),
        "test-worker": TestAdapter(),
    }

    adapter = adapters.get(worker_id) or SwarmMaintenanceAdapter()
    return adapter.run(baton, workspace_dir)


class BaseAdapter:
    def run(self, baton: dict, workspace_dir: str) -> dict:
        raise NotImplementedError()

    def _create_branch(self, branch_name: str, workspace_dir: str):
        base_branch = "master"
        res = subprocess.run(["git", "show-ref", "refs/heads/main"], cwd=workspace_dir, capture_output=True)
        if res.returncode == 0:
            base_branch = "main"
        subprocess.run(["git", "checkout", base_branch], cwd=workspace_dir, capture_output=True)
        subprocess.run(["git", "checkout", "-B", branch_name, base_branch], cwd=workspace_dir, capture_output=True)

    def _get_diff(self, workspace_dir: str) -> str:
        subprocess.run(["git", "add", "-N", "."], cwd=workspace_dir, capture_output=True)
        res = subprocess.run(["git", "diff"], cwd=workspace_dir, capture_output=True, text=True)
        diff = res.stdout
        if not diff.strip():
            # If no uncommitted diff, check if we are on a worker branch and get diff against master/main
            branch_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_dir, capture_output=True, text=True)
            branch = branch_res.stdout.strip()
            if branch not in ("", "master", "main", "HEAD"):
                base_branch = "master"
                res = subprocess.run(["git", "show-ref", "refs/heads/main"], cwd=workspace_dir, capture_output=True)
                if res.returncode == 0:
                    base_branch = "main"
                diff_res = subprocess.run(["git", "diff", f"{base_branch}...HEAD"], cwd=workspace_dir, capture_output=True, text=True)
                return diff_res.stdout
        return diff

    def _run_tests(self, workspace_dir: str) -> str:
        import os
        env = os.environ.copy()
        libs_aja_core = str(Path(workspace_dir) / "libs" / "aja-core")
        if "PYTHONPATH" in env:
            env["PYTHONPATH"] = f"{libs_aja_core}{os.pathsep}{env['PYTHONPATH']}"
        else:
            env["PYTHONPATH"] = libs_aja_core

        test_dir = Path(workspace_dir) / "tests" / "python" / "unit"
        if not test_dir.exists():
            return ""

        try:
            res = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/python/unit", "--maxfail=1", "-v"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                env=env
            )
            # If pytest is successfully run as module, return results
            if "no module named pytest" not in (res.stderr or "").lower():
                return res.stdout if res.returncode == 0 else f"Tests failed:\n{res.stdout}\n{res.stderr}"
        except Exception:
            pass

        # Fallback to default pytest binary on PATH with set PYTHONPATH
        res = subprocess.run(
            ["pytest", "tests/python/unit", "--maxfail=1", "-v"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            env=env
        )
        return res.stdout if res.returncode == 0 else f"Tests failed:\n{res.stdout}\n{res.stderr}"

    def _missing_cli(self, cli_name: str) -> dict:
        return {
            "status": "failed",
            "error": f"Required CLI is not available on PATH: {cli_name}",
            "output": "",
            "diff": "",
            "tests": "",
            "rollback_path": "",
        }


class TestAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        run_id = baton.get("run_id", "test-run")
        action = task.split("test:", 1)[1].strip() if "test:" in task else "success"

        tool_path = Path(workspace_dir) / "scripts" / "test_idempotent_tool.py"
        if not tool_path.exists():
            tool_path = Path(workspace_dir) / "tests" / "python" / "test_idempotent_tool.py"
        if not tool_path.exists():
            return {
                "status": "failed",
                "error": "test_idempotent_tool.py was not found in scripts/ or tests/python/.",
                "output": "",
            }

        cmd = [sys.executable, str(tool_path), run_id, action]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=workspace_dir)
        if res.returncode == 0:
            return {
                "status": "completed",
                "output": res.stdout,
                "diff": "",
                "tests": "",
                "rollback_path": "",
            }
        return {
            "status": "failed",
            "error": res.stderr or res.stdout,
            "output": res.stdout,
        }


class CopilotAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        dod = "\n".join(baton.get("definition_of_done", []))

        if not shutil.which("gh"):
            return self._missing_cli("gh")

        branch_name = f"copilot-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)

        prompt = f"{task}\n\nDefinition of done:\n{dod}".strip()
        cmd = ["gh", "copilot", "suggest", prompt]
        res = subprocess.run(cmd, cwd=workspace_dir, capture_output=True, text=True)
        if res.returncode != 0:
            return {
                "status": "failed",
                "error": res.stderr or res.stdout,
                "output": res.stdout,
                "diff": self._get_diff(workspace_dir),
                "tests": "",
                "rollback_path": f"git checkout main && git branch -D {branch_name}",
            }

        return {
            "status": "completed",
            "output": res.stdout or f"Copilot suggestion generated for '{task}'.",
            "diff": self._get_diff(workspace_dir),
            "tests": self._run_tests(workspace_dir),
            "rollback_path": f"git checkout main && git branch -D {branch_name}",
        }


class GeminiAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        if not shutil.which("gemini"):
            return self._missing_cli("gemini")

        branch_name = f"gemini-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        res = subprocess.run(["gemini", "-p", task], cwd=workspace_dir, capture_output=True, text=True)

        if res.returncode != 0:
            return {
                "status": "failed",
                "error": res.stderr or res.stdout,
                "output": res.stdout,
                "diff": self._get_diff(workspace_dir),
                "tests": "",
                "rollback_path": f"git checkout main && git branch -D {branch_name}",
            }

        return {
            "status": "completed",
            "output": res.stdout,
            "diff": self._get_diff(workspace_dir),
            "tests": self._run_tests(workspace_dir),
            "rollback_path": f"git checkout main && git branch -D {branch_name}",
        }


class AiderAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        if not shutil.which("aider"):
            return self._missing_cli("aider")

        branch_name = f"aider-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        res = subprocess.run(["aider", "--message", task, "--yes-always"], cwd=workspace_dir, capture_output=True, text=True)

        if res.returncode != 0:
            return {
                "status": "failed",
                "error": res.stderr or res.stdout,
                "output": res.stdout,
                "diff": self._get_diff(workspace_dir),
                "tests": "",
                "rollback_path": f"git checkout main && git branch -D {branch_name}",
            }

        return {
            "status": "completed",
            "output": res.stdout,
            "diff": self._get_diff(workspace_dir),
            "tests": self._run_tests(workspace_dir),
            "rollback_path": f"git checkout main && git branch -D {branch_name}",
        }


class CodexAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        if not shutil.which("codex"):
            return self._missing_cli("codex")

        branch_name = f"codex-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        res = subprocess.run(["codex", "exec", task], cwd=workspace_dir, capture_output=True, text=True)

        if res.returncode != 0:
            return {
                "status": "failed",
                "error": res.stderr or res.stdout,
                "output": res.stdout,
                "diff": self._get_diff(workspace_dir),
                "tests": "",
                "rollback_path": f"git checkout main && git branch -D {branch_name}",
            }

        return {
            "status": "completed",
            "output": res.stdout,
            "diff": self._get_diff(workspace_dir),
            "tests": self._run_tests(workspace_dir),
            "rollback_path": f"git checkout main && git branch -D {branch_name}",
        }


class SwarmMaintenanceAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        output = f"Recorded maintenance task '{task}'. No external worker CLI was requested."

        return {
            "status": "completed",
            "output": output,
            "diff": "",
            "tests": "",
            "rollback_path": "No rollback needed for maintenance tasks.",
        }


class NativeWorkerAdapter(BaseAdapter):
    async def run_async(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        
        # Local import to prevent circular dependency
        from aja.orchestration.swarm import SwarmEngine
        import os
        import json
        
        worker_model = os.getenv("AJA_WORKER_MODEL", "")
        if not worker_model:
            try:
                cfg_file = Path(workspace_dir) / "aja.json"
                if cfg_file.exists():
                    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                    worker_model = cfg.get("swarm_settings", {}).get("models", {}).get("worker", "")
            except Exception:
                pass
        if not worker_model:
            worker_model = "copilot:gpt-4o-mini"

        engine = SwarmEngine(model=worker_model, dry_run=False)
        
        # Enforce autonomous, non-interactive system prompt for background worker execution
        engine.presenter.direct_system_prompt = (
            "You are AJA (Assistant of Joint Agents), an elite AI assistant operating in a strictly "
            "NON-INTERACTIVE, AUTONOMOUS background worker context.\n"
            f"The absolute path of the authorized project root is: {workspace_dir}\n"
            "Your objective is to accomplish the assigned task using direct tool execution.\n\n"
            "CRITICAL RULES FOR AUTONOMOUS WORKERS:\n"
            "1. DO NOT ASK THE USER QUESTIONS or request clarification. Stdin is not connected. "
            "If you need info or parameters, search files, read logs, make logical assumptions, or search the web. Do not wait for input.\n"
            "2. You MUST execute tools or shell commands until you have fully completed the task. "
            "An empty execution with no files modified will fail verification.\n"
            f"3. All changes and new files must be written inside the project root: {workspace_dir}.\n"
            "4. Speak like a premium developer-fluent AI assistant. Summarize your actions at the end.\n"
            "5. Prefer to use structured JSON tools (read_file, write_file, multi_replace) for filesystem edits."
        )
        
        branch_name = f"native-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        
        try:
            # Capture the worker's actual answer via a light output contract
            # so read-only missions (research/fetch) still produce verifiable
            # output. Weak local models that cannot emit valid JSON fall back
            # to a plain synthesis instead of failing.
            from aja.llm_structured import StructuredOutputError

            answer = None
            if _model_supports_contracts(worker_model):
                try:
                    res = await engine.execute_direct(
                        task,
                        output_contract={
                            "type": "object",
                            "required": ["summary"],
                            "properties": {"summary": {"type": "string"}},
                        },
                    )
                    if isinstance(res, dict):
                        answer = res.get("result", {}).get("summary")
                        if answer:
                            _mark_model_contract_capable(worker_model, True)
                except StructuredOutputError:
                    _mark_model_contract_capable(worker_model, False)
                    logger.warning(
                        "[WorkerAdapter] model '%s' cannot honor output contracts; "
                        "skipping contract attempts for future tasks.",
                        worker_model,
                    )
                    answer = None
                except Exception:
                    answer = None

            if not answer:
                await engine.execute_direct(task)

            diff = self._get_diff(workspace_dir)
            # Tests validate CODE changes and are OPT-IN for native workers:
            # live workspaces are often the repo root itself, where blind
            # pytest runs pick up unrelated dirty state and fail read-only
            # research missions. Enable with AJA_WORKER_RUN_TESTS=1.
            import os as _os
            tests = self._run_tests(workspace_dir) if _os.getenv("AJA_WORKER_RUN_TESTS", "") == "1" else ""

            output = (
                f"Task completed. Answer: {answer}"
                if answer
                else f"Native worker successfully executed task: {task}"
            )
            return {
                "status": "completed",
                "output": output,
                "diff": diff,
                "tests": tests,
                "rollback_path": f"git checkout main && git branch -D {branch_name}",
            }
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e),
                "output": "",
                "diff": self._get_diff(workspace_dir),
                "tests": "",
                "rollback_path": f"git checkout main && git branch -D {branch_name}",
            }
