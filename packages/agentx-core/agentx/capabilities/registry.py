from .base import Capability

class CapabilityRegistry:
    def __init__(self):
        self.capabilities = {}

    def register(self, cap: Capability):
        self.capabilities[cap.name] = cap

    def get(self, name: str) -> Capability:
        if name not in self.capabilities:
            raise KeyError(f"Capability '{name}' not found in registry.")
        return self.capabilities[name]

# Global registry instance
registry = CapabilityRegistry()
