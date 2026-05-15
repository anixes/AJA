import json
import os
from typing import Dict, List, Optional

METHODS_FILE = os.path.join(".agentx", "methods.json")

class MethodLibrary:
    """
    Persistent storage for HTN decomposition methods.
    Allows the planner to "learn" and reuse proven patterns.
    """

    @staticmethod
    def _ensure_dir():
        os.makedirs(".agentx", exist_ok=True)

    @classmethod
    def load(cls) -> Dict[str, List[str]]:
        """Load all saved methods from disk."""
        if not os.path.exists(METHODS_FILE):
            return {}
        try:
            with open(METHODS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[MethodLibrary] Failed to load methods: {e}")
            return {}

    @classmethod
    def save(cls, name: str, subtasks: List[str]) -> bool:
        """
        Save a new decomposition method.
        
        Parameters
        ----------
        name : str
            The name or signature of the goal (e.g. 'build_feature')
        subtasks : List[str]
            A list of task descriptions that make up the method.
        """
        cls._ensure_dir()
        methods = cls.load()
        methods[name] = subtasks
        
        try:
            with open(METHODS_FILE, "w") as f:
                json.dump(methods, f, indent=2)
            return True
        except Exception as e:
            print(f"[MethodLibrary] Failed to save method: {e}")
            return False

    @classmethod
    def format_for_prompt(cls) -> str:
        """Format the currently known methods into a string for the LLM prompt."""
        methods = cls.load()
        if not methods:
            return "{}"
        return json.dumps(methods, indent=2)
