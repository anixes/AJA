import sys
import os
from scripts.health_check import run_health_check
from scripts.core.gateway import UnifiedGateway
from pathlib import Path

# Config
PROVIDER = "nvidia" # Or your preferred provider
KEY = os.getenv("AI_KEY", "dummy")
MODEL = "llama-3"

def heal_system(territory="src/prod"):
    # 1. DETECT (Scoping to territory)
    print(f"[-] Territory Scan: {territory}")
    
    # Simple logic: Find all .ts files in the territory to check
    files = list(Path(territory).glob("*.ts")) if os.path.isdir(territory) else [Path(territory)]
    
    for file_path in files:
        # In this demo, we use our specific health check logic
        healthy, error_log = run_health_check(str(file_path)) 
        if healthy:
            continue

        print(f"\n--- INITIATING SELF-HEAL PROTOCOL FOR {file_path.name} ---")
        
        # 2. DIAGNOSE (Using AI Gateway)
        code = file_path.read_text()
        
        prompt = f"""
        You are the AgentX Self-Healing Agent. 
        A production file is crashing.
        
        FILE: {file_path}
        CODE:
        {code}
        
        ERROR LOG:
        {error_log}
        
        TASK: Fix the bug. Return ONLY the full corrected code for the file. 
        No explanations, no markdown backticks.
        """
        
        gateway = UnifiedGateway(PROVIDER, KEY)
        
        print(f"[AI] Calling AI to diagnose and repair {file_path.name}...")
        
        if KEY != "dummy":
            fixed_code = gateway.chat(MODEL, prompt)
        else:
            # Dummy Mode: Simulated Fix for the typo
            fixed_code = code.replace("return finaPrice;", "return finalPrice;")
            print("  - [DUMMY MODE] Applying pre-programmed fix...")

        # 3. REPAIR
        print("[FS] Applying repairs to filesystem...")
        file_path.write_text(fixed_code)
        
        # 4. VERIFY
        print("\n[V] RE-RUNNING HEALTH CHECK...")
        healthy_after, new_log = run_health_check(str(file_path))
        
        if healthy_after:
            print("\n[+] HEALING SUCCESSFUL! System is back online.")
        else:
            print("\n[!] Healing failed. Escalating to human developer.")
            
        # Stop after fixing one file
        return
        
    print("\n[-] Territory scan complete. No further issues found.")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "src/prod"
    heal_system(target)
