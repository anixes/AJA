import shutil
import subprocess
import sys
from pathlib import Path


def dispatch_worker(worker_id: str, baton: dict, workspace_dir: str) -> dict:
    """
    Dispatch a baton to the best available worker adapter.
    """
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
        subprocess.run(["git", "checkout", "-b", branch_name], cwd=workspace_dir, capture_output=True)

    def _get_diff(self, workspace_dir: str) -> str:
        res = subprocess.run(["git", "diff"], cwd=workspace_dir, capture_output=True, text=True)
        return res.stdout

    def _run_tests(self, workspace_dir: str) -> str:
        res = subprocess.run(["pytest", "--maxfail=1", "-v"], cwd=workspace_dir, capture_output=True, text=True)
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
