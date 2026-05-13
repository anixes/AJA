import os
import subprocess
import json
from agentx.security.stripper import CommandStripper
from agentx.orchestration.gateway import UnifiedGateway
from agentx.runtime.sandbox import execute_command
from agentx.utils.tokenjuice import TokenJuice


class AJAGuard:
    """
    The AJA Guard (formerly SafeShell) intercepts and inspects commands for security risks
    before AgentX execution.
    """

    # Tier 1: Truly dangerous binaries that almost always need analysis
    DANGEROUS_BINARIES = {
        "dd",
        "mkfs",
        "shutdown",
        "reboot",
        "chmod",
        "chown",
        "kill",
        "pkill",
    }

    # Tier 2: Sensitive binaries that are common in dev but need monitoring
    SENSITIVE_BINARIES = {
        "rm",
        "mv",
        "wget",
        "curl",
        "bash",
        "sh",
        "zsh",
        "python",
        "npm",
        "gh",
    }

    def __init__(self, provider: str, api_key: str, model: str):
        self.gateway = UnifiedGateway(provider, api_key)
        self.model = model
        self.juice = TokenJuice()

    def is_known_safe(self, cmd_str: str, root: str, args: list) -> bool:
        """Rule-based check for common safe developer patterns."""
        if root == "python":
            if "--version" in args or "-m pip install" in cmd_str:
                return True
        if root == "npm":
            if "install" in args or "i" in args or "list" in args:
                return True
        if root == "gh":
            if "repo view" in cmd_str or "issue list" in cmd_str:
                return True
        if root == "git":
            return True  # Generally safe
        if root == "rm":
            # Only allow rm on specific temp or local patterns without prompt
            safe_targets = ["temp", "tmp", "cache", "node_modules", "dist", "build"]
            if any(t in cmd_str for t in safe_targets) and not (
                "/" in cmd_str and len(cmd_str.split("/")) < 3
            ):
                return True
        return False

    def check_and_execute(self, cmd_str: str):
        # 1. Strip the command to find the root binary
        stripper = CommandStripper(cmd_str)
        stripper.strip()
        report = stripper.report()
        root = report["Root Binary"]
        args = report["Arguments"]

        # 2. Check risk level
        needs_analysis = False
        if root in self.DANGEROUS_BINARIES:
            needs_analysis = True
        elif root in self.SENSITIVE_BINARIES:
            if not self.is_known_safe(cmd_str, root, args):
                needs_analysis = True

        if needs_analysis:
            print(f"\n⚠️  MONITOR: Analyzing '{root}' usage...")

            # Skip AI for simple sensitive commands if they look okayish, just a quick warning
            if root in self.SENSITIVE_BINARIES and len(cmd_str) < 50:
                print(f"[*] Quick-check passed for sensitive command: {cmd_str}")
            else:
                print("--- AI Risk Analysis Gate ---")
                prompt = f"""
                Analyze this shell command and explain the potential risks.
                Command: {cmd_str}
                Root binary: {root}
                Arguments: {args}
                
                Be concise. If it's a standard dev command (like installing a package or moving a file in a project), say it's LOW RISK.
                """
                explanation = self.gateway.chat(
                    self.model,
                    prompt,
                    system="You are a security-focused AI assistant.",
                )
                print(f"\nAI RISK ANALYSIS:\n{explanation}")

            confirm = input("\nExecute? (y/N): ")
            if confirm.lower() != "y":
                print("Execution cancelled.")
                return

        # 3. Execute using the Sandbox Stack
        print(f"🚀 Executing: {cmd_str}")

        # Determine network need
        network_cmds = [
            "npm install",
            "pip install",
            "cargo build",
            "gh ",
            "git clone",
            "curl",
            "wget",
        ]
        allow_network = any(n in cmd_str for n in network_cmds)

        try:
            res = execute_command(cmd_str, allow_network=allow_network)

            stdout = res.get("stdout", "")
            stderr = res.get("stderr", "")

            # Determine juice context
            tool_context = None
            if "npm" in cmd_str:
                tool_context = "npm/install"
            elif "pip" in cmd_str:
                tool_context = "pip/install"

            compacted_out = self.juice.compact(stdout, tool_context=tool_context)
            compacted_err = self.juice.compact(stderr, tool_context=tool_context)

            if compacted_out:
                print(compacted_out)
            if compacted_err:
                print(f"Error: {compacted_err}")

            if not res["success"]:
                print(f"[*] Command failed with exit code {res.get('exit_code')}")

        except Exception as e:
            print(f"Failed to execute: {str(e)}")


if __name__ == "__main__":
    print("--- 🛡️ Welcome to AJA Guard ---")

    # In a real app, these would come from .env
    provider = input("Enter Provider (nvidia/groq/together): ").strip()
    key = input("Enter API Key: ").strip()
    model = input("Enter Model (e.g. nvidia/llama-3.1-nemotron-70b-instruct): ").strip()

    shell = AJAGuard(provider, key, model)

    while True:
        try:
            cmd = input("\nAJA Guard > ").strip()
            if cmd in ["exit", "quit"]:
                break
            if not cmd:
                continue

            shell.check_and_execute(cmd)
        except KeyboardInterrupt:
            break

    print("\nAJA Guard closed. Stay safe!")
