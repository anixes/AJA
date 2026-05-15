from .base import Capability, CapabilityResult
from agentx.runtime.sandbox import is_safe, execute_command, docker_available
from agentx.utils.tokenjuice import TokenJuice

import logging
logger = logging.getLogger(__name__)

juice = TokenJuice()

def run_in_sandbox(cmd: str, timeout: int = 60, allow_network: bool = None) -> CapabilityResult:
    """Runs a command with safety bounds.  Prefers Docker; falls back to direct."""
    if not is_safe(cmd):
        return CapabilityResult(
            success=False, output={},
            error="Command rejected by security sandbox."
        )

    # Auto-detect network requirement if not explicitly set
    if allow_network is None:
        network_cmds = [
            "npm install", "npm i", "yarn add", "pnpm install",
            "pip install", "pip3 install", "poetry install",
            "cargo build", "cargo install",
            "gh repo", "gh auth", "git clone", "git push", "git pull", "git fetch",
            "curl ", "wget ", "apt-get", "apt "
        ]
        allow_network = any(n in cmd for n in network_cmds)

    try:
        res = execute_command(cmd, timeout=timeout, allow_network=allow_network)

        if "warning" in res:
            logger.warning("[sandbox] %s", res["warning"])

        # Determine tool context for TokenJuice
        tool_context = None
        if "git status" in cmd:
            tool_context = "git/status"
        elif "npm install" in cmd:
            tool_context = "npm/install"
        elif "pip install" in cmd:
            tool_context = "pip/install"
        elif "cargo build" in cmd:
            tool_context = "cargo/build"

        stdout = res.get("stdout", "")
        compacted = juice.compact(stdout, tool_context=tool_context)
        
        if len(stdout) > len(compacted):
            stats = juice.get_stats(stdout, compacted)
            logger.info(f"TokenJuice: {stats.reduction_percent}% reduction ({stats.original_chars} -> {stats.compacted_chars})")

        if res["success"]:
            return CapabilityResult(
                success=True,
                output={
                    "stdout": compacted,
                    "mode": res.get("mode", "unknown"),
                    "juiced": len(stdout) > len(compacted),
                    "network_allowed": allow_network
                }
            )
        else:
            return CapabilityResult(
                success=False,
                output={"stdout": compacted},
                error=res.get("stderr", "Unknown error"),
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
        "allow_network": "bool (optional, default auto-detected)",
    }

    def execute(self, inputs: dict) -> CapabilityResult:
        cmd = inputs.get("cmd")
        if not cmd:
            return CapabilityResult(success=False, output={}, error="Missing 'cmd' in inputs.")
        timeout = inputs.get("timeout", 60)
        allow_network = inputs.get("allow_network")
        return run_in_sandbox(cmd, timeout=timeout, allow_network=allow_network)
