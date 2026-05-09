import subprocess
import os
import json
from pathlib import Path

def dispatch_worker(worker_id: str, baton: dict, workspace_dir: str) -> dict:
    """
    Dispatch the task to the appropriate worker adapter based on worker_id.
    Returns a dict with {"status": "completed"|"failed", "output": str, "error": str, "diff": str, "tests": str}
    """
    adapters = {
        "github-copilot-cli": CopilotAdapter(),
        "gemini-cli": GeminiAdapter(),
        "aider-worker": AiderAdapter(),
        "codex-cli": CodexAdapter(),
        "swarm-maintenance": SwarmMaintenanceAdapter(),
        "test-worker": TestAdapter(),
    }
    
    
    adapter = adapters.get(worker_id)
    if not adapter:
        # Fallback to SwarmMaintenanceAdapter if unknown
        adapter = SwarmMaintenanceAdapter()
        
    return adapter.run(baton, workspace_dir)
class TestAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        run_id = baton.get("run_id", "test-run")
        
        # Parse task for action (simple "test: <action>")
        action = "success"
        if "test:" in task:
            action = task.split("test:")[1].strip()
            
        print(f"[TestAdapter] Dispatching to test_idempotent_tool with action: {action}")
        
        PYTHON = sys.executable
        cmd = [
            PYTHON, 
            os.path.join(workspace_dir, "scripts", "test_idempotent_tool.py"),
            run_id,
            action
        ]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                return {
                    "status": "completed",
                    "output": res.stdout,
                    "diff": "",
                    "tests": "",
                    "rollback_path": ""
                }
            else:
                return {
                    "status": "failed",
                    "error": res.stderr or res.stdout,
                    "output": res.stdout
                }
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e)
            }
    if not adapter:
        # Fallback to SwarmMaintenanceAdapter if unknown
        adapter = SwarmMaintenanceAdapter()
        
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
        # Placeholder for test execution
        res = subprocess.run(["pytest", "--maxfail=1", "-v"], cwd=workspace_dir, capture_output=True, text=True)
        return res.stdout if res.returncode == 0 else f"Tests failed:\n{res.stdout}\n{res.stderr}"

class CopilotAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        dod = "\n".join(baton.get("definition_of_done", []))
        
        branch_name = f"copilot-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        
        # Simulated Copilot CLI dispatch
        # prefer /plan first before implementation, use /delegate, /diff
        print(f"[CopilotAdapter] /plan: Planning task '{task}'")
        print(f"[CopilotAdapter] /delegate: Executing task...")
        
        # Simulate work
        output = f"Executed task '{task}' via GitHub Copilot CLI.\nDoD:\n{dod}"
        
        diff = self._get_diff(workspace_dir)
        tests = self._run_tests(workspace_dir)
        
        return {
            "status": "completed",
            "output": output,
            "diff": diff,
            "tests": tests,
            "rollback_path": f"git checkout main && git branch -D {branch_name}"
        }

class GeminiAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        
        branch_name = f"gemini-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        
        print(f"[GeminiAdapter] Generating code for '{task}'")
        output = f"Executed task '{task}' via Gemini CLI."
        
        return {
            "status": "completed",
            "output": output,
            "diff": self._get_diff(workspace_dir),
            "tests": self._run_tests(workspace_dir),
            "rollback_path": f"git checkout main && git branch -D {branch_name}"
        }

class AiderAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        
        branch_name = f"aider-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        
        print(f"[AiderAdapter] Aider is applying changes for '{task}'")
        output = f"Executed task '{task}' via Aider."
        
        return {
            "status": "completed",
            "output": output,
            "diff": self._get_diff(workspace_dir),
            "tests": self._run_tests(workspace_dir),
            "rollback_path": f"git checkout main && git branch -D {branch_name}"
        }

class CodexAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        
        branch_name = f"codex-worker-{baton.get('id', 'task')}"
        self._create_branch(branch_name, workspace_dir)
        
        print(f"[CodexAdapter] Codex is generating completion for '{task}'")
        output = f"Executed task '{task}' via Codex CLI."
        
        return {
            "status": "completed",
            "output": output,
            "diff": self._get_diff(workspace_dir),
            "tests": self._run_tests(workspace_dir),
            "rollback_path": f"git checkout main && git branch -D {branch_name}"
        }

class SwarmMaintenanceAdapter(BaseAdapter):
    def run(self, baton: dict, workspace_dir: str) -> dict:
        task = baton.get("task", "")
        
        # Maintenance worker does not necessarily create branches
        print(f"[SwarmMaintenanceAdapter] Maintaining: '{task}'")
        output = f"Executed task '{task}' via Swarm Maintenance Worker."
        
        return {
            "status": "completed",
            "output": output,
            "diff": "",
            "tests": "",
            "rollback_path": "No rollback needed for maintenance tasks."
        }
