import os
import json
import logging
import shlex
import asyncio
import threading
from typing import List, Dict, Any, Optional
from aja.config import PROJECT_ROOT
from aja.security.command_guard import classify_command
from aja.runtime.execution import ExecutionRequest, get_default_execution_manager

logger = logging.getLogger(__name__)

class ToolExecutor:
    """
    Safely executes tools (shell commands) requested by the LLM.
    Uses CommandStripper logic internally.
    """

    BLACKBOX_COMMANDS = {"rm -rf /", "mkfs", "dd", "shutdown", "reboot"}

    def __init__(self):
        self.history = []

    def _run_execution(self, command: str, cwd: str) -> Dict[str, Any]:
        async def _run():
            return await get_default_execution_manager().run(
                ExecutionRequest(
                    command=command,
                    cwd=cwd,
                    timeout=30,
                    workspace_mode="isolated",
                    metadata={"legacy_api": "ToolExecutor.execute"},
                )
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(_run())
        else:
            box: Dict[str, Any] = {}
            err: Dict[str, BaseException] = {}

            def runner():
                try:
                    box["result"] = asyncio.run(_run())
                except BaseException as exc:
                    err["error"] = exc

            thread = threading.Thread(target=runner, daemon=True)
            thread.start()
            thread.join()
            if err:
                raise err["error"]
            result = box["result"]

        return {
            "status": "success" if result.success else "failed",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "code": result.exit_code,
            "session_id": result.session_id,
            "manifest_path": result.manifest_path,
        }

    def execute(self, command: str, cwd: str = None) -> Dict[str, Any]:
        """Executes a single command and returns the result."""
        logger.info(f"ToolExecutor: Executing '{command}'")
        
        # 1. Safety Check
        classification = classify_command(command)
        if classification["decision"] == "deny":
            return {"status": "error", "message": "Command blocked: " + "; ".join(classification["reasons"])}

        # 2. Preparation
        target_cwd = cwd or str(PROJECT_ROOT)
        
        try:
            output = self._run_execution(command, target_cwd)
            self.history.append({"command": command, "result": output})
            return output
            
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return {"status": "error", "message": str(e)}

    def parse_and_run(self, text: str) -> List[Dict[str, Any]]:
        """Extracts code blocks or json tool calls from text and runs them."""
        # Simple heuristic: Look for `bash` or `sh` blocks
        results = []
        if "```bash" in text:
            parts = text.split("```bash")
            for part in parts[1:]:
                cmd = part.split("```")[0].strip()
                if cmd:
                    results.append(self.execute(cmd))
        elif "```sh" in text:
            parts = text.split("```sh")
            for part in parts[1:]:
                cmd = part.split("```")[0].strip()
                if cmd:
                    results.append(self.execute(cmd))
        
        return results
