from typing import Dict, Any

class SubAgent:
    name: str = "base.agent"
    max_steps: int = 10
    allowed_tools: list[str] = []

    def run(self, task: str, context: Dict[str, Any], limits: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a task under strict boundaries.
        Returns the result payload.
        """
        raise NotImplementedError("SubAgent must implement run()")

class CodingAgent(SubAgent):
    name = "agent.coder"
    max_steps = 15
    allowed_tools = ["file.read", "file.write", "terminal.exec"]

    def run(self, task: str, context: Dict[str, Any], limits: Dict[str, Any]) -> Dict[str, Any]:
        # Placeholder for Claude Code or similar specific coding agent logic
        # For now, we simulate execution
        print(f"[CodingAgent] Executing task: {task} within limits {limits}")
        return {"status": "success", "code_changes": True}

class BrowserAgent(SubAgent):
    name = "agent.browser"
    max_steps = 20
    allowed_tools = ["browser.navigate", "browser.click", "browser.read"]

    def run(self, task: str, context: Dict[str, Any], limits: Dict[str, Any]) -> Dict[str, Any]:
        # Placeholder for Playwright/Browser interaction logic
        print(f"[BrowserAgent] Executing task: {task} within limits {limits}")
        return {"status": "success", "page_visited": True}
