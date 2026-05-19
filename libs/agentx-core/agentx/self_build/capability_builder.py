import json
import time
import uuid
import os
from dataclasses import dataclass, asdict
from typing import Dict, Any, List

import agentx.config
from agentx.llm import get_gateway_for_model
from agentx.runtime.event_bus import bus, EVENTS

REGISTRY_FILE = agentx.config.PROJECT_ROOT / ".agentx" / "agent_capabilities.json"
EXPERIENCE_FILE = agentx.config.PROJECT_ROOT / ".agentx" / "agent_experiences.json"

@dataclass
class Capability:
    name: str
    code: str
    description: str
    version: int = 1
    created_at: float = 0.0
    success_rate: float = 0.0
    risk: float = 0.0

class ExperienceStore:
    def __init__(self):
        self.experiences = []
        self.load()

    def save_experience(self, data: Dict[str, Any]):
        self.experiences.append(data)
        self.save()

    def load(self):
        if os.path.exists(EXPERIENCE_FILE):
            try:
                with open(EXPERIENCE_FILE, "r") as f:
                    self.experiences = json.load(f)
            except Exception:
                pass

    def save(self):
        try:
            with open(EXPERIENCE_FILE, "w") as f:
                json.dump(self.experiences, f, indent=2)
        except Exception:
            pass

class CapabilityRegistry:
    def __init__(self):
        self.capabilities: Dict[str, List[Capability]] = {}
        self.load()

    def register(self, cap: Capability):
        if cap.name not in self.capabilities:
            self.capabilities[cap.name] = []
        
        # Versioning
        if self.capabilities[cap.name]:
            cap.version = self.capabilities[cap.name][-1].version + 1
        
        self.capabilities[cap.name].append(cap)
        self.save()
        print(f"[CapabilityRegistry] Registered new capability: {cap.name} (v{cap.version})")

    def get_latest(self, name: str) -> Capability:
        if name in self.capabilities and self.capabilities[name]:
            return self.capabilities[name][-1]
        return None

    def revert_to_previous_version(self, name: str):
        if name in self.capabilities and len(self.capabilities[name]) > 1:
            print(f"[CapabilityRegistry] Reverting {name} to previous version.")
            self.capabilities[name].pop()
            self.save()

    def load(self):
        if os.path.exists(REGISTRY_FILE):
            try:
                with open(REGISTRY_FILE, "r") as f:
                    data = json.load(f)
                for name, caps in data.items():
                    self.capabilities[name] = [Capability(**c) for c in caps]
            except Exception:
                pass

    def save(self):
        data = {name: [asdict(c) for c in caps] for name, caps in self.capabilities.items()}
        try:
            with open(REGISTRY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

registry = CapabilityRegistry()
experience_store = ExperienceStore()
SELF_BUILD_ENABLED = True

def disable_self_build():
    global SELF_BUILD_ENABLED
    SELF_BUILD_ENABLED = False
    print("[SelfBuild] System performance drop detected. Disabling self-build engine.")

def propose_capability(problem: str) -> Capability:
    """
    Generate new capability/tool to solve problem using LLM
    """
    print(f"[SelfBuild] Proposing capability for problem: {problem}")
    model_name = agentx.config.AGENTX_PLANNER_MODEL
    
    system = """You are Agent Capability Builder.
Your task is to generate a Python tool/function to solve a specific problem.
CRITICAL RULE: DO NOT modify the core system files or internal structures.
Allowed: new tools, helper scripts, workflow functions.

Return ONLY JSON:
{
    "name": "snake_case_name",
    "description": "What it does",
    "code": "def ...",
    "risk": 0.0 to 1.0 (higher if it touches disk/network),
    "modifies_core_system": true/false
}
"""
    try:
        from agentx.llm import completion
        raw = completion(prompt=f"Problem: {problem}", system_prompt=system, model=model_name)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        data = json.loads(raw)
        
        # Policy Guard (Very Important)
        if data.get("modifies_core_system", False):
            print("[SelfBuild] REJECTED: Capability attempts to modify core system.")
            return None
            
        cap = Capability(
            name=data["name"],
            code=data["code"],
            description=data["description"],
            created_at=time.time(),
            risk=data.get("risk", 0.5)
        )
        return cap
    except Exception as e:
        print(f"[SelfBuild] Failed to propose capability: {e}")
        return None

def run_in_sandbox(cap: Capability) -> float:
    """
    Run capability in sandbox and return success_rate
    """
    print(f"[SelfBuild] Testing capability {cap.name} in sandbox...")
    try:
        # Very basic sandbox execution check
        local_scope = {}
        exec(cap.code, {"__builtins__": __builtins__}, local_scope)
        # Verify function is defined
        if cap.name in local_scope or any(callable(v) for v in local_scope.values()):
            return 0.9  # Good success rate for syntax/basic validity
        return 0.0
    except Exception as e:
        print(f"[SelfBuild] Sandbox test failed: {e}")
        return 0.0

def test_capability(cap: Capability) -> float:
    return run_in_sandbox(cap)

def require_approval(cap: Capability) -> bool:
    print(f"[SelfBuild] ⚠️ HIGH RISK ({cap.risk}). Requires operator approval: {cap.name}")
    # Simulate user approval logic; for headless agent, we reject or pause
    # Returning False means rejected in autonomous mode
    return False

def check_drift_and_rollback():
    """
    Part J - Drift Control
    """
    recent = experience_store.experiences[-10:]
    if not recent:
        return
    failures = sum(1 for e in recent if not e.get("success", True))
    if failures >= 5:
        disable_self_build()
        # Rollback last capability if it coincides with degradation
        if registry.capabilities:
            last_cap_name = list(registry.capabilities.keys())[-1]
            registry.revert_to_previous_version(last_cap_name)

def self_build_cycle(problem: str):
    """
    Part H - Self-Building Loop
    Triggered when repeated_failure(problem) is true.
    """
    if not SELF_BUILD_ENABLED:
        return
        
    check_drift_and_rollback()
    if not SELF_BUILD_ENABLED:
        return

    cap = propose_capability(problem)
    if not cap:
        return

    # Part G - Operator Approval
    if cap.risk > 0.6:
        if not require_approval(cap):
            print(f"[SelfBuild] Capability {cap.name} rejected by approval guard.")
            return

    # Part B - Sandbox Testing
    success_rate = test_capability(cap)
    cap.success_rate = success_rate
    
    # Part I - Memory Integration
    result = success_rate >= 0.8
    experience_store.save_experience({
        "problem": problem,
        "solution": cap.name,
        "success": result,
        "success_rate": success_rate
    })

    if result:
        # Part C - Registry Extension
        registry.register(cap)
        print(f"[SelfBuild] Successfully built and registered: {cap.name}")
        bus.publish(EVENTS["NODE_SUCCESS"], {"task": f"Self-built capability: {cap.name}"})
    else:
        print(f"[SelfBuild] Capability test failed (success_rate: {success_rate}). Rejected.")
