from .base import Capability, CapabilityResult
from aja.agents.base import SubAgent

class AgentCapability(Capability):
    def __init__(self, agent: SubAgent):
        self.aja = agent
        self.name = agent.name
        self.input_schema = {
            "task": "str",
            "context": "dict (optional)"
        }

    def execute(self, inputs: dict) -> CapabilityResult:
        task = inputs.get("task")
        if not task:
            return CapabilityResult(success=False, output={}, error="Missing 'task' in inputs.")
            
        context = inputs.get("context", {})
        
        # Enforce execution envelope based on agent policies
        limits = {
            "max_tokens": 5000,
            "timeout": 60,
            "max_steps": self.aja.max_steps
        }
        
        try:
            result = self.aja.run(task, context, limits=limits)
            return CapabilityResult(success=True, output=result)
        except Exception as e:
            return CapabilityResult(success=False, output={}, error=str(e))
