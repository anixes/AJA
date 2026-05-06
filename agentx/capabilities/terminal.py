from .base import Capability, CapabilityResult
from agentx.runtime.sandbox import is_safe, execute_command, docker_available

import logging
logger = logging.getLogger(__name__)


def run_in_sandbox(cmd: str, timeout: int = 60) -> CapabilityResult:
    """Runs a command with safety bounds.  Prefers Docker; falls back to direct."""
    if not is_safe(cmd):
        return CapabilityResult(
            success=False, output={},
            error="Command rejected by security sandbox."
        )

    try:
        res = execute_command(cmd, timeout=timeout)

        if "warning" in res:
            logger.warning("[sandbox] %s", res["warning"])

        if res["success"]:
            return CapabilityResult(
                success=True,
                output={
                    "stdout": res["stdout"],
                    "mode": res.get("mode", "unknown"),
                }
            )
        else:
            return CapabilityResult(
                success=False,
                output={"stdout": res.get("stdout", "")},
                error=res["stderr"],
            )
    except ValueError as e:        # unsafe command
        return CapabilityResult(success=False, output={}, error=str(e))
    except Exception as e:
        return CapabilityResult(success=False, output={}, error=str(e))


class TerminalExec(Capability):
    name = "terminal.exec"
    input_schema = {
        "cmd": "str",
        "timeout": "int (optional, default 60)",
    }

    def execute(self, inputs: dict) -> CapabilityResult:
        cmd = inputs.get("cmd")
        if not cmd:
            return CapabilityResult(success=False, output={}, error="Missing 'cmd' in inputs.")
        timeout = inputs.get("timeout", 60)
        return run_in_sandbox(cmd, timeout=timeout)
