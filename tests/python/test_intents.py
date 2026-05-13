import json
from agent.security.stripper import CommandStripper

# Mock AI Translation Logic (Simulating what happens in the TUI)
INTENT_MAPPING = {
    "list all files": "ls -la",
    "delete the temp folder": "sudo rm -rf ./temp",
    "check network connections": "nc -zv google.com 80",
    "find the main file": "find . -name main.tsx"
}

# Local Risk Map (Mirroring the TUI/BashTool logic)
RISK_DB = {
    'rm': ('CRITICAL', 'Permanent deletion.'),
    'nc': ('CRITICAL', 'Netcat (Backdoor Risk).'),
    'sudo': ('HIGH', 'Root Privilege Escalation.')
}

def run_intent_test(intent):
    print(f"\n[USER INTENT]: {intent}")
    
    # 1. AI Translation (Simulated)
    cmd = INTENT_MAPPING.get(intent, "ls")
    print(f"[AI TRANSLATION]: {cmd}")
    
    # 2. Semantic Stripping
    s = CommandStripper(cmd)
    s.strip()
    root = s.report()['Root Binary']
    
    # 3. Security Audit
    level, reason = RISK_DB.get(root, ("SAFE", "No immediate threat."))
    
    status = "BLOCKED" if level in ["CRITICAL", "HIGH"] else "ALLOWED"
    
    print(f"[SECURITY AUDIT]: {status}")
    print(f"  - Binary: {root}")
    print(f"  - Level: {level}")
    print(f"  - Reason: {reason}")
    print("-" * 30)

if __name__ == "__main__":
    print("--- AGENTX HUMAN INTENT INTEGRATION TEST ---")
    run_intent_test("list all files")
    run_intent_test("delete the temp folder")
    run_intent_test("check network connections")
    run_intent_test("find the main file")
