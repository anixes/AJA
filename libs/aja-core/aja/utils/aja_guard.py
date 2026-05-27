import os
import subprocess
import json
import logging
from aja.security.command_guard import ASK_BINARIES, classify_command
from aja.orchestration.gateway import LLMGateway
from aja.runtime.sandbox import execute_command
from aja.utils.tokenjuice import TokenJuice

logger = logging.getLogger(__name__)


class AJAGuard:
    """
    AJA Guard (formerly SafeShell) — the command safety layer inside AJA Core.
    AJA uses it to keep shell execution explainable, auditable, and operator-approved
    when risk is present. Intercepts and inspects commands before AJA execution.
    """

    SENSITIVE_BINARIES = set(ASK_BINARIES)

    def __init__(self, provider: str, api_key: str, model: str):
        self.gateway = LLMGateway(provider, api_key)
        self.model = model
        self.juice = TokenJuice()

    def classify_command(self, cmd_str: str) -> dict:
        return classify_command(cmd_str)

    def check_and_execute(self, cmd_str: str):
        # 1. Strip the command and classify risk before execution.
        classification = self.classify_command(cmd_str)
        root = classification["root"]
        args = classification["args"]
        needs_analysis = classification["needs_analysis"]

        if classification["decision"] == "deny":
            print("Execution blocked: " + "; ".join(classification["reasons"]))
            return

        if needs_analysis:
            print(f"\n[*] MONITOR: Analyzing '{root}' usage...")

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
            logger.exception("AJA Guard failed to execute command")
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
