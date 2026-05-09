import os
import subprocess
import json
from scripts.core.stripper import CommandStripper
from scripts.core.gateway import UnifiedGateway

class SafeShell:
    """
    An integrated 'Safe Shell' that inspects commands using Claude-style stripping
    and uses the Unified Gateway to explain risks before execution.
    """
    
    DANGEROUS_BINARIES = {
        "rm", "mv", "chmod", "chown", "dd", "mkfs", "shutdown", "reboot",
        "kill", "pkill", "wget", "curl", "bash", "sh", "zsh", "python"
    }

    def __init__(self, provider: str, api_key: str, model: str):
        self.gateway = UnifiedGateway(provider, api_key)
        self.model = model

    def check_and_execute(self, cmd_str: str):
        # 1. Strip the command to find the root binary
        stripper = CommandStripper(cmd_str)
        stripper.strip()
        report = stripper.report()
        root = report["Root Binary"]

        # 2. Check if dangerous
        if root in self.DANGEROUS_BINARIES:
            print(f"\n⚠️  WARNING: Potentially dangerous command detected: '{root}'")
            print("--- Analyzing risks via AI Gateway ---")
            
            prompt = f"""
            Analyze this shell command and explain the potential risks to my system.
            Command: {cmd_str}
            Root binary: {root}
            Arguments: {report['Arguments']}
            
            Be concise and tell me if I should run this or not.
            """
            explanation = self.gateway.chat(self.model, prompt, system="You are a security-focused AI assistant.")
            print(f"\nAI RISK ANALYSIS:\n{explanation}")
            
            confirm = input("\nDo you still want to execute this command? (y/N): ")
            if confirm.lower() != 'y':
                print("Execution cancelled.")
                return

        # 3. Execute
        print(f"🚀 Executing: {cmd_str}")
        try:
            result = subprocess.run(cmd_str, shell=True, capture_output=True, text=True)
            if result.stdout: print(result.stdout)
            if result.stderr: print(f"Error: {result.stderr}")
        except Exception as e:
            print(f"Failed to execute: {str(e)}")

if __name__ == "__main__":
    print("--- 🛡️ Welcome to SafeShell (Claude-Inspired) ---")
    
    # In a real app, these would come from .env
    provider = input("Enter Provider (nvidia/groq/together): ").strip()
    key = input("Enter API Key: ").strip()
    model = input("Enter Model (e.g. nvidia/llama-3.1-nemotron-70b-instruct): ").strip()
    
    shell = SafeShell(provider, key, model)
    
    while True:
        try:
            cmd = input("\nSafeShell > ").strip()
            if cmd in ["exit", "quit"]: break
            if not cmd: continue
            
            shell.check_and_execute(cmd)
        except KeyboardInterrupt:
            break
    
    print("\nSafeShell closed. Stay safe!")
