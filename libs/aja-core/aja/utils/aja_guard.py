import os
import logging
from typing import Any, Callable, Dict, Optional
from aja.security.command_guard import ASK_BINARIES, classify_command
from aja.runtime.sandbox import execute_command
from aja.utils.tokenjuice import TokenJuice
from aja.utils.redact import redact_secrets

logger = logging.getLogger(__name__)


class AJAGuard:
    """
    AJA Guard (formerly SafeShell) — the command safety layer inside AJA Core.
    AJA uses it to keep shell execution explainable, auditable, and operator-approved
    when risk is present. Intercepts and inspects commands before AJA execution.

    check_and_execute returns a structured result dict so callers can branch on the
    outcome instead of parsing stdout:
        {"status": "executed"|"denied"|"cancelled"|"failed", "exit_code": int|None,
         "classification": dict|None, "error": str|None}
    """

    SENSITIVE_BINARIES = set(ASK_BINARIES)

    def __init__(
        self,
        provider: str = "",
        api_key: str = "",
        model: str = "",
        gateway=None,
        input_fn: Optional[Callable[[str], str]] = None,
    ):
        if gateway is not None:
            self.gateway = gateway
        else:
            from aja.orchestration.gateway import LLMGateway
            self.gateway = LLMGateway(provider, api_key)
        self.model = model
        self.juice = TokenJuice()
        self.input_fn = input_fn or input

    def classify_command(self, cmd_str: str) -> dict:
        return classify_command(cmd_str)

    def check_and_execute(self, cmd_str: str) -> Dict[str, Any]:
        # 1. Strip the command and classify risk before execution.
        classification = self.classify_command(cmd_str)
        root = classification["root"]
        args = classification["args"]
        needs_analysis = classification["needs_analysis"]
        result: Dict[str, Any] = {
            "status": "failed",
            "exit_code": None,
            "classification": classification,
            "error": None,
        }

        if classification["decision"] == "deny":
            reason = "; ".join(classification["reasons"])
            logger.warning("Execution blocked: %s", reason)
            print("Execution blocked: " + reason)
            result["status"] = "denied"
            result["error"] = reason
            return result

        if needs_analysis:
            print(f"\n[*] MONITOR: Analyzing '{root}' usage...")

            confirm = self.input_fn(f"\nExecute '{cmd_str}'? (y/N): ")
            if confirm.strip().lower() != "y":
                logger.info("Execution cancelled by operator for command: %s", cmd_str)
                print("Execution cancelled.")
                result["status"] = "cancelled"
                return result

        # 2. Execute using the Sandbox Stack
        # Interactive operator surface: keep console output but redact
        # credential-looking substrings from the command and its output.
        print(f"🚀 Executing: {redact_secrets(cmd_str)}")

        # Determine network need
        network_cmds = [
            "npm install",
            "pip install",
            "cargo build",
            "git clone",
            "curl",
            "wget",
        ]
        allow_network = any(cmd_str.startswith(n) for n in network_cmds)

        try:
            res = execute_command(cmd_str, allow_network=allow_network)

            stdout = res.get("stdout", "")
            stderr = res.get("stderr", "")

            compacted_out = self.juice.squeeze(stdout)
            compacted_err = self.juice.squeeze(stderr)

            if compacted_out:
                print(redact_secrets(compacted_out))
            if compacted_err:
                print(f"Error: {redact_secrets(compacted_err)}")

            exit_code = res.get("exit_code")
            result["exit_code"] = exit_code
            if not res.get("success", False):
                msg = f"Command failed with exit code {exit_code}"
                print(f"[*] {msg}")
                result["error"] = msg
            else:
                result["status"] = "executed"
            return result

        except Exception as e:
            logger.exception("AJA Guard failed to execute command")
            print(f"Failed to execute: {str(e)}")
            result["error"] = str(e)
            return result


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
