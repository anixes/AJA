from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Static
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.binding import Binding
import os
import subprocess
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Resolve project root portably
def find_project_root():
    """Finds the AgentX project root by looking for agentx.json."""
    current = Path(__file__).resolve().parent
    for _ in range(4):
        if (current / "agentx.json").exists():
            return current
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent

PROJECT_ROOT = find_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


from scripts.core.stripper import CommandStripper
from scripts.core.gateway import UnifiedGateway

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

class SafeShellTUI(App):
    """A premium TUI for SafeShell."""
    
    CSS = """
    Screen {
        background: #1a1a1a;
    }
    
    #main-layout {
        height: 100%;
        width: 100%;
    }
    
    #log-container {
        height: 1fr;
        border: solid #333;
        background: #000;
        padding: 1;
    }
    
    #side-panel {
        width: 40;
        border-left: solid #333;
        padding: 1;
        background: #222;
    }
    
    #input-bar {
        dock: bottom;
        height: 3;
        border-top: solid #333;
    }
    
    .risk-warning {
        color: #ffaa00;
        border: double #ffaa00;
    }
    
    .risk-danger {
        color: #ff5555;
        border: double #ff5555;
    }
    
    .log-entry {
        margin-bottom: 1;
    }
    
    .command-text {
        color: #00ff00;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear_log", "Clear Log"),
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
                    yield Input(placeholder="Enter command here...", id="input-bar")
                with Vertical(id="side-panel"):
                    yield Static("🛡️ STATUS", id="status-header")
                    yield Static(f"Provider: {self.gateway.provider.upper()}")
                    yield Static(f"Model: {self.model}")
                    yield RiskPanel("No risks detected.", id="risk-display")
        yield Footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        raw_input = event.value.strip()
        if not raw_input:
            return
            
        self.query_one("#input-bar").value = ""
        
        # 0. INTENT DETECTION: Is it English or a Command?
        greetings = {"hi", "hello", "hey", "help", "clear", "exit", "quit", "who are you"}
        words = raw_input.split()
        first_word = words[0].lower() if words else ""
        
        # Quick handling for built-in TUI commands
        if first_word in ["clear", "cls"]:
            self.action_clear_log()
            return
        if first_word in ["exit", "quit"]:
            self.exit()
            return

        import shutil
        is_intent = True
        
        # Comprehensive list of common binaries and builtins
        system_binaries = self.dangerous_binaries | {
            "ls", "dir", "cd", "pwd", "mkdir", "touch", "cat", "echo", "grep", 
            "git", "python", "node", "npm", "cargo", "go", "gcc", "g++", "make",
            "type", "copy", "move", "del", "cls", "clear", "ipconfig", "ping",
            "netstat", "ssh", "scp", "docker", "kubectl", "code", "vim", "nano"
        }

        # Heuristic: If first word is a known binary, it's likely a command
        if shutil.which(first_word) or first_word in system_binaries:
            is_intent = False
        # If it's a known greeting or short question, it's definitely an intent
        elif first_word in greetings or len(words) > 5 or "?" in raw_input:
            is_intent = True
        # If it has flags or common command operators, it's likely a command
        elif any(c in raw_input for c in [" -", " /", "|", ">", "<", "&", ";"]):
            is_intent = False
        # Fallback: if it's multiple words and doesn't look like a command, it's an intent
        elif len(words) > 1:
            is_intent = True
        else:
            is_intent = False

        cmd = raw_input
        if is_intent:
            # Quick local response for greetings/help
            if first_word in ["hi", "hello", "hey"]:
                 self.log_command("AI: Hello! I'm AgentX, your security-conscious terminal assistant. How can I help you today?")
                 return
            if first_word == "help":
                 self.log_command("AI: I can help you run terminal commands safely. Just type what you want to do (e.g., 'list files') or a direct command (e.g., 'ls'). I'll audit it for security risks before execution.")
                 return

            self.log_command(f"Interpreting intent: {raw_input}...")
            intent_prompt = (
                "You are AgentX, a security-conscious terminal assistant. "
                f"The user said: '{raw_input}'.\n"
                "1. If it is a greeting or general question, reply helpfully and briefly.\n"
                "2. If it is a request for a terminal action, output the bash command wrapped in <cmd>bash command</cmd>.\n"
                "Example: <cmd>ls -la</cmd>"
            )
            
            try:
                api_key = self.gateway.api_key
                if api_key == "dummy":
                    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
                
                if api_key and api_key != "dummy":
                    self.gateway.api_key = api_key
                    # chat is sync in scripts.core.gateway.UnifiedGateway
                    import asyncio
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, self.gateway.chat, self.model, intent_prompt)
                    
                    if "<cmd>" in response and "</cmd>" in response:
                        import re
                        match = re.search(r"<cmd>(.*?)</cmd>", response, re.DOTALL)
                        if match:
                            cmd = match.group(1).strip()
                            explanation = response.replace(match.group(0), "").strip()
                            if explanation:
                                self.log_command(f"AI Info: {explanation}")
                            self.log_command(f"Intent resolved to: [bold cyan]{cmd}[/]")
                        else:
                            self.log_command(f"AI: {response}")
                            return
                    else:
                        self.log_command(f"AI: {response}")
                        return
                else:
                    if first_word in greetings:
                        self.log_command("AI (Offline Dummy): Hello! I'm in dummy mode because no API key was found. How can I help?")
                        return
                    cmd = "ls -la"
                    self.log_command(f"Intent resolved to (Dummy Fallback): [bold cyan]{cmd}[/]")
            except Exception as e:
                self.query_one("#risk-display").update_risk(f"Intent interpretation failed: {str(e)}", "warning")
                return

        # Security Risk Analysis
        stripper = CommandStripper(cmd)
        stripper.strip()
        report = stripper.report()
        root = report["Root Binary"]
        
        RISK_DB = {
            'rm': ('CRITICAL', 'Permanent deletion.'),
            'nc': ('CRITICAL', 'Netcat (Backdoor Risk).'),
            'netcat': ('CRITICAL', 'Netcat (Backdoor Risk).'),
            'sudo': ('HIGH', 'Root Privilege Escalation.'),
            'chmod': ('HIGH', 'Permission Tampering.'),
            'dd': ('CRITICAL', 'Disk Manipulation/Wiping.'),
            'reboot': ('HIGH', 'System Reset.'),
            'kill': ('MEDIUM', 'Process Termination.')
        }

        level, reason = RISK_DB.get(root, ("SAFE", "No immediate threat detected."))
        risk_color = "red" if level in ["CRITICAL", "HIGH"] else "yellow" if level == "MEDIUM" else "green"

        self.query_one("#risk-display").update(
            f"[bold blue]SECURITY AUDIT[/]\n"
            f"Binary: [b]{root}[/b]\n"
            f"Level: [{risk_color}]{level}[/]\n"
            f"Reason: {reason}\n"
        )

        if root in self.dangerous_binaries or level != "SAFE":
            self.query_one("#risk-display").update_risk(f"Analyzing {level} risk via AI Gateway...", "warning")
            prompt = (
                f"Analyze this command for security risks: '{cmd}'. "
                "Highlight specific risks. Be concise (2 sentences max)."
            )
            try:
                if self.gateway.api_key != "dummy":
                    import asyncio
                    loop = asyncio.get_event_loop()
                    explanation = await loop.run_in_executor(None, self.gateway.chat, self.model, prompt)
                else:
                    explanation = f"DUMMY MODE: Command '{cmd}' uses {root} ({level} risk)."
                self.query_one("#risk-display").update_risk(explanation, "danger" if level in ["CRITICAL", "HIGH"] else "warning")
            except Exception:
                pass
        
        self.execute_command(cmd)

    def log_command(self, cmd: str):
        log = self.query_one("#log-container")
        log.mount(Static(f"> {cmd}", classes="log-entry"))
        log.scroll_end()

    def execute_command(self, cmd: str):
        try:
            import platform
            shell = True
            if platform.system() == "Windows":
                if cmd.startswith("ls"): cmd = cmd.replace("ls", "dir", 1)

            result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)
            log = self.query_one("#log-container")
            if result.stdout:
                log.mount(Static(result.stdout))
            if result.stderr:
                log.mount(Static(f"[red]{result.stderr}[/]"))
            log.scroll_end()
        except Exception as e:
            self.query_one("#log-container").mount(Static(f"[red]Error: {str(e)}[/]"))

    def action_clear_log(self):
        log = self.query_one("#log-container")
        for child in log.children:
            child.remove()

if __name__ == "__main__":
    # Prioritize agentx.json in current directory
    cwd_config = Path(os.getcwd()) / "agentx.json"
    config_path = cwd_config if cwd_config.exists() else PROJECT_ROOT / "agentx.json"
    
    provider = "google"
    model = "gemini-flash-latest"
    
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            swarm = config.get("swarm_settings", {})
            mode = swarm.get("operating_mode", "online")
            
            # Default fallback
            provider = "google"
            model = "gemini-flash-latest"

            if mode == "offline":
                provider = "llama_cpp"
                model = "gemma-4-e2b"
            else:
                # For online or hybrid, we prefer the planner model for the TUI shell
                model_settings = swarm.get("models", {})
                planner_model = model_settings.get("planner", "google:gemini-flash-latest")
                if planner_model and ":" in planner_model:
                    p, m = planner_model.split(":", 1)
                    provider, model = p, m
                else:
                    model = planner_model
                    if "gemini" in model.lower(): provider = "google"
                    elif "gemma" in model.lower() or "llama" in model.lower(): provider = "llama_cpp"
        except Exception:
            pass

    key = os.getenv(f"{provider.upper()}_API_KEY") or os.getenv("GEMINI_API_KEY") or "dummy"
    app = SafeShellTUI(provider, key, model)
    app.run()
