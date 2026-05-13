from typing import Dict, Any, Optional

class CapabilityResult:
    """Execution contract for all capabilities."""
    def __init__(self, success: bool, output: Dict[str, Any], error: Optional[str] = None, state_delta: Optional[Dict[str, Any]] = None):
        self.success = success
        self.output = output
        self.error = error
        self.state_delta = state_delta or {}

class Capability:
    """Base class for executable primitives."""
    name: str = "base.capability"
    input_schema: dict = {}

    def execute(self, inputs: dict) -> CapabilityResult:
        raise NotImplementedError("Capability subclasses must implement execute().")
