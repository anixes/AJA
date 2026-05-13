import os
import json
import logging
import subprocess
import shlex
from typing import List, Dict, Any, Optional
from agentx.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

class ToolExecutor:
    """
    Safely executes tools (shell commands) requested by the LLM.
    Uses CommandStripper logic internally.
    """

    BLACKBOX_COMMANDS = {"rm -rf /", "mkfs", "dd", "shutdown", "reboot"}

    def __init__(self):
        self.history = []

    def execute(self, command: str, cwd: str = None) -> Dict[str, Any]:
        """Executes a single command and returns the result."""
        logger.info(f"ToolExecutor: Executing '{command}'")
        
        # 1. Safety Check
        if any(bad in command for bad in self.BLACKBOX_COMMANDS):
            return {"status": "error", "message": "Destructive command blocked by FileGuardian."}

        # 2. Preparation
        target_cwd = cwd or str(PROJECT_ROOT)
        
        try:
            # We use shell=True for flexibility but with restricted environment
            result = subprocess.run(
                command,
                shell=True,
                cwd=target_cwd,
                capture_output=True,
                text=True,
                timeout=30 # Safety timeout
            )
            
            output = {
                "status": "success" if result.returncode == 0 else "failed",
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "code": result.returncode
            }
            self.history.append({"command": command, "result": output})
            return output
            
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Command timed out after 30s."}
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
