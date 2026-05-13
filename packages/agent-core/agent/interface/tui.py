"""
agent/interface/tui.py
========================
Premium TUI for Agent / Assistant.
Refactored for 100% Pure Agent (Arrow/LanceDB memory).
"""

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Static
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.binding import Binding
import os
import subprocess
import json
import sys
import platform
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Resolve project root portably
def find_project_root():
    current = Path(__file__).resolve().parent
    for _ in range(4):
        if (current / "agent.json").exists() or (current / ".git").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent

PROJECT_ROOT = find_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agent.security.stripper import CommandStripper
from agent.orchestration.gateway import UnifiedGateway
from agent.presence.state import get_system_state

ASSISTANT_LOGO = """
 █████╗      ██╗ █████╗ 
██╔══██╗     ██║██╔══██╗
███████║     ██║███████║
██╔══██║██   ██║██╔══██║
██║  ██║╚█████╔╝██║  ██║
╚═╝  ╚═╝ ╚════╝ ╚═╝  ╚═╝
[bold cyan]Assistant Bot Interface[/]
"""

class RiskPanel(Static):
    """A panel to display AI Risk Analysis."""
    def update_risk(self, explanation: str, level: str = "info"):
        self.update(f"[{level}]AI RISK ANALYSIS:[\n]{explanation}")
        if "danger" in level or "danger" in explanation.lower():
            self.set_classes("risk-danger")
        elif "warning" in level or "warning" in explanation.lower():
            self.set_classes("risk-warning")
        else:
            self.set_classes("")

class AgentShell(App):
    """
    A premium TUI replacing 'SafeShellTUI'.
    Now fully integrated with the Assistant persona and Agent Core.
    """
    
    CSS = """
    Screen {
        background: #0d1117;
    }
    
    #main-layout {
        height: 100%;
        width: 100%;
    }
    
    #log-container {
        height: 1fr;
        border: solid #30363d;
        background: #010409;
        padding: 1;
    }
    
    #side-panel {
        width: 45;
        border-left: solid #30363d;
        padding: 1;
        background: #161b22;
    }
    
    #input-bar {
        dock: bottom;
        height: 3;
        border-top: solid #30363d;
        background: #0d1117;
    }
    
    .risk-warning {
        color: #e3b341;
        border: double #e3b341;
    }
    
    .risk-danger {
        color: #f85149;
        border: double #f85149;
    }
    
    .log-entry {
        margin-bottom: 1;
    }
    
    .assistant-msg {
        color: #58a6ff;
        text-style: bold;
    }
    
    .status-item {
        margin-bottom: 1;
        padding: 0 1;
    }
    
    #logo {
        height: 8;
        content-align: center middle;
        margin-bottom: 1;
        color: #58a6ff;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear_log", "Clear Log"),
        Binding("f5", "refresh_state", "Refresh Status"),
    ]

    def __init__(self, provider, key, model):
        super().__init__()
        self.gateway = UnifiedGateway(provider, key)
        self.model = model
        self.dangerous_binaries = {
            "rm", "mv", "chmod", "chown", "dd", "mkfs", "shutdown", "reboot",
            "kill", "pkill", "wget", "curl", "bash", "sh", "zsh", "python"
        }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-layout"):
            with Horizontal():
                with Vertical():
                    yield ScrollableContainer(id="log-container")
                    yield Input(placeholder="Ask Assistant or enter a command...", id="input-bar")
                with Vertical(id="side-panel"):
                    yield Static(ASSISTANT_LOGO, id="logo")
                    yield Static("--- SYSTEM METRICS (ARROW) ---", classes="status-item")
                    yield Static("Tasks: Loading...", id="task-stat", classes="status-item")
                    yield Static("Health: Loading...", id="health-stat", classes="status-item")
                    yield Static("Mode: Loading...", id="mode-stat", classes="status-item")
                    yield Static("\n--- SECURITY ---", classes="status-item")
                    yield RiskPanel("System idle. Awaiting instruction.", id="risk-display")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh_state()
        self.log_assistant("System online. Agent swarm is standing by.")

    def action_refresh_state(self) -> None:
        """Fetch real-time state from Arrow tables via get_system_state."""
        state = get_system_state()
        self.query_one("#task-stat").update(f"Tasks: {state['active_tasks']} active | {state['pending_tasks']} pending")
        health_color = "green" if state["is_healthy"] else "red"
        self.query_one("#health-stat").update(f"Health: [{health_color}]{'HEALTHY' if state['is_healthy'] else 'UNSTABLE'}[/]")
        self.query_one("#mode-stat").update(f"Load: {state['load_level']}")

    def log_assistant(self, msg: str):
        log = self.query_one("#log-container")
        log.mount(Static(f"[bold cyan]Assistant:[/] {msg}", classes="log-entry assistant-msg"))
        log.scroll_end()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        raw_input = event.value.strip()
        if not raw_input:
            return
            
        self.query_one("#input-bar").value = ""
        
        # Determine if it's a natural language intent or a direct command
        import shutil
        first_word = raw_input.split()[0].lower() if raw_input else ""
        system_binaries = self.dangerous_binaries | {"ls", "dir", "cd", "pwd", "git", "cat", "echo", "mkdir"}
        
        is_intent = True
        if shutil.which(first_word) or first_word in system_binaries or any(c in raw_input for c in [" -", " /", "|", ">"]):
            is_intent = False
            
        if is_intent:
            self.log_assistant(f"Processing request: '{raw_input}'")
            # Logic here would typically call the intent parser
            # For brevity in TUI, we delegate to a quick chat call
            import asyncio
            loop = asyncio.get_event_loop()
            prompt = f"User request: '{raw_input}'. If it's a command, wrap in <cmd>bash</cmd>. Otherwise reply as Assistant."
            response = await loop.run_in_executor(None, self.gateway.chat, self.model, prompt)
            
            if "<cmd>" in response:
                import re
                match = re.search(r"<cmd>(.*?)</cmd>", response)
                if match:
                    cmd = match.group(1).strip()
                    self.log_assistant(f"I've prepared a command for you: [bold yellow]{cmd}[/]")
                    self.audit_and_execute(cmd)
                    return
            self.log_assistant(response)
        else:
            self.audit_and_execute(raw_input)

    def audit_and_execute(self, cmd: str):
        # Security Audit logic (reuse existing stripper pattern)
        stripper = CommandStripper(cmd)
        stripper.strip()
        report = stripper.report()
        root = report["Root Binary"]
        
        risk_level = "SAFE"
        if root in self.dangerous_binaries:
            risk_level = "WARNING"
            
        self.query_one("#risk-display").update_risk(f"Target: {root}\nStatus: {risk_level}", "warning" if risk_level != "SAFE" else "info")
        
        # Execute
        try:
            if platform.system() == "Windows" and cmd.startswith("ls"):
                cmd = cmd.replace("ls", "dir", 1)
            
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            log = self.query_one("#log-container")
            log.mount(Static(f"[bold green]> {cmd}[/]"))
            if result.stdout: log.mount(Static(result.stdout))
            if result.stderr: log.mount(Static(f"[red]{result.stderr}[/]"))
            log.scroll_end()
            self.action_refresh_state()
        except Exception as e:
            self.log_assistant(f"Execution failed: {e}")

if __name__ == "__main__":
    # Integration point for main.py
    provider = "google"
    model = "gemini-2.0-flash" # Use a stable default
    key = os.getenv("GEMINI_API_KEY") or "dummy"
    
    app = AgentShell(provider, key, model)
    app.run()
