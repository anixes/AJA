from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseModelProvider(ABC):
    """Abstract Base Class for all model inference providers in AJA."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def chat_completions(
        self, 
        messages: List[Dict[str, str]], 
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Runs model inference for standard chat completion requests."""
        pass

    @abstractmethod
    def check_requirements(self) -> bool:
        """Verifies if the required credentials, API tokens, or environments are present."""
        pass


class BaseTool(ABC):
    """Abstract Base Class for custom tools in the AJA ecosystem."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the canonical name of the tool."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Return a concise description of the tool for the LLM schema."""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Executes the tool's core logic. Returns a JSON string output."""
        pass

    @abstractmethod
    def check_requirements(self) -> bool:
        """Verifies if the tool has all runtime requirements met (dependencies, keys, etc.)."""
        pass
