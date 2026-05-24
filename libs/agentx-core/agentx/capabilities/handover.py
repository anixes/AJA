from .base import Capability, CapabilityResult
from agentx.runtime.handover import BatonManager
import logging

logger = logging.getLogger(__name__)

class HandoverCapability(Capability):
    """
    Capability to move sessions between devices/channels.
    """
    name = "agent.handover"
    input_schema = {
        "action": "str (generate, pickup)",
        "code": "str (required for pickup)",
        "current_state": "dict (required for generate)"
    }

    def __init__(self):
        self.manager = BatonManager()

    def execute(self, inputs: dict) -> CapabilityResult:
        action = inputs.get("action")
        
        if action == "generate":
            state = inputs.get("current_state")
            if not state:
                return CapabilityResult(success=False, output={}, error="Missing 'current_state' to handover")
            
            objective = inputs.get("objective") or state.get("objective") or state.get("goal") or "session handover"
            code = self.manager.capture(objective, state)
            return CapabilityResult(
                success=True, 
                output={
                    "code": code,
                    "message": f"Session handover ready. Use code '{code}' on your other device/channel to pickup."
                }
            )

        if action == "pickup":
            code = inputs.get("code")
            if not code:
                return CapabilityResult(success=False, output={}, error="Missing handover 'code'")
            
            state = self.manager.pickup(code)
            if not state:
                return CapabilityResult(success=False, output={}, error="Invalid or expired handover code")
            
            return CapabilityResult(
                success=True, 
                output={
                    "state": state,
                    "message": "Session successfully restored."
                }
            )

        return CapabilityResult(success=False, output={}, error=f"Unknown action '{action}'")
