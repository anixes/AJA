import sys
import time
import json
import random
import asyncio
from typing import Dict, Any, List
from rich.console import Console, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.table import Table
from rich.align import Align
from rich.box import ROUNDED, DOUBLE, HEAVY

console = Console()

# Define the skin themes
SKINS = {
    "default": {
        "name": "Default Protocol",
        "border_color": "blue",
        "title_color": "bold cyan",
        "accent_color": "white",
        "box_style": ROUNDED,
        "spinners": ["|", "/", "-", "\\"]
    },
    "cyberpunk": {
        "name": "Cyberpunk Neon Grid",
        "border_color": "magenta",
        "title_color": "bold bright_cyan",
        "accent_color": "green",
        "box_style": DOUBLE,
        "spinners": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    },
    "ares": {
        "name": "Ares Crimson Tactical",
        "border_color": "red",
        "title_color": "bold bright_yellow",
        "accent_color": "bright_red",
        "box_style": HEAVY,
        "spinners": ["▖", "▘", "▝", "▗"]
    }
}

class TerminalDashboard:
    """
    High-fidelity Terminal UI Dashboard representing the HTN Execution Graph,
    Worker Telemetry Logs, and Interactive SWAT Controls.
    Supports dynamic cyberpunk/ares/default skins.
    """
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.current_skin_key = "cyberpunk"
        self.paused = False
        self.running = True
        self.input_history = []
        
        # Mock/Real state
        self.nodes = [
            {"id": "node-1", "task": "Load project environment configuration", "status": "COMPLETED"},
            {"id": "node-2", "task": "Check soft-dependency package imports", "status": "COMPLETED"},
            {"id": "node-3", "task": "Scan libs/aja-core/ for core vulnerabilities", "status": "RUNNING"},
            {"id": "node-4", "task": "Validate Pydantic configurations schema", "status": "PENDING"},
            {"id": "node-5", "task": "Initialize LanceDB table stores", "status": "PENDING"},
            {"id": "node-6", "task": "Synthesize report and export walkthrough", "status": "PENDING"}
        ]
        
        self.logs = [
            "[09:40:01] INFO [BatonManager] Scanning local mmap batons directory...",
            "[09:40:02] INFO [MemoryManager] LanceDB connection established on ./.aja/lancedb",
            "[09:40:03] INFO [doctor] psutil dependency checked successfully (Soft-Dependency verified)",
            "[09:40:15] INFO [SwarmEngine] Dispatched plan decomposition: 'Perform security audit'",
            "[09:40:16] SUCCESS [Planner] HTN Decompositions succeeded. Generated 6 primitive steps.",
            "[09:40:18] INFO [SwarmEngine] Executing step 1/6: Load project environment configuration",
            "[09:40:22] SUCCESS [Worker-1] Step 1/6 successfully executed. Status: COMPLETED",
            "[09:40:23] INFO [SwarmEngine] Executing step 2/6: Check soft-dependency package imports",
            "[09:40:25] SUCCESS [Worker-2] Step 2/6 successfully executed. Status: COMPLETED",
            "[09:40:26] INFO [SwarmEngine] Executing step 3/6: Scan libs/aja-core/ for core vulnerabilities",
            "[09:40:27] WARNING [AJAGuard] Shell execution audited: grep command detected. Checked against AJAGuard rules."
        ]
        self.log_counter = 0

    def get_skin(self) -> Dict[str, Any]:
        return SKINS[self.current_skin_key]

    def toggle_skin(self):
        keys = list(SKINS.keys())
        current_idx = keys.index(self.current_skin_key)
        self.current_skin_key = keys[(current_idx + 1) % len(keys)]

    def generate_simulated_activity(self):
        """Simulate real-time task progression and worker logs."""
        self.log_counter += 1
        if self.log_counter % 8 == 0 and not self.paused:
            # Shift node status
            for node in self.nodes:
                if node["status"] == "RUNNING":
                    node["status"] = "COMPLETED"
                    self.logs.append(f"[{time.strftime('%H:%M:%S')}] SUCCESS [Worker] Task completed: {node['task']}")
                    break
            
            # Run next pending node
            for node in self.nodes:
                if node["status"] == "PENDING":
                    node["status"] = "RUNNING"
                    self.logs.append(f"[{time.strftime('%H:%M:%S')}] INFO [SwarmEngine] Dispatched task: {node['task']}")
                    break
                    
        # Add random worker logs occasionally
        if random.random() < 0.15 and not self.paused:
            workers = ["SwarmEngine", "BatonManager", "LanceDB", "AJAGuard", "Worker-Fleet"]
            levels = ["INFO", "DEBUG", "SUCCESS", "WARNING"]
            messages = [
                "Synchronizing Arrow Handover Baton execution index...",
                "Trace telemetry propagation context verified in thread-local storage.",
                "Compacting LanceDB core_tasks indices...",
                "Security audit checkpoint passed without deviations.",
                "Soft-dependency import checked dynamically.",
                "Baton pickle serialized to IPC mmap storage."
            ]
            log_line = f"[{time.strftime('%H:%M:%S')}] {random.choice(levels)} [{random.choice(workers)}] {random.choice(messages)}"
            self.logs.append(log_line)
            if len(self.logs) > 30:
                self.logs.pop(0)

    def render_htn_panel(self) -> RenderableType:
        skin = self.get_skin()
        spinner = skin["spinners"][int(time.time() * 4) % len(skin["spinners"])]
        
        table = Table.grid(expand=True, padding=0)
        table.add_column("Status", width=12, justify="left")
        table.add_column("Task Description", justify="left")
        
        for idx, n in enumerate(self.nodes):
            status = n["status"]
            desc = n["task"]
            
            if status == "COMPLETED":
                status_text = Text("✔ COMPLETED", style="bold green")
                desc_style = "dim white"
            elif status == "RUNNING":
                status_text = Text(f"{spinner} RUNNING", style="bold yellow")
                desc_style = "bold yellow"
            elif status == "FAILED":
                status_text = Text("✘ FAILED", style="bold red")
                desc_style = "bold red"
            else:
                status_text = Text("⧖ PENDING", style="dim cyan")
                desc_style = "dim cyan"
                
            table.add_row(status_text, Text(f"{idx+1}. {desc}", style=desc_style))
            table.add_row("", "") # spacing row
            
        return Panel(
            Align.left(table),
            title=f"[{skin['title_color']}]█ HTN Plan DAG Graph [/{skin['title_color']}]",
            border_style=skin["border_color"],
            box=skin["box_style"]
        )

    def render_logs_panel(self) -> RenderableType:
        skin = self.get_skin()
        formatted_logs = []
        for line in self.logs[-12:]:  # Limit to last 12 lines
            if "SUCCESS" in line:
                style = "green"
            elif "WARNING" in line:
                style = "yellow"
            elif "ERROR" in line:
                style = "bold red"
            elif "DEBUG" in line:
                style = "dim white"
            else:
                style = "bright_cyan"
                
            formatted_logs.append(Text(line, style=style))
            
        log_content = Text("\n").join(formatted_logs)
        return Panel(
            log_content,
            title=f"[{skin['title_color']}]█ Trailing Worker Telemetry Logs [/{skin['title_color']}]",
            border_style=skin["border_color"],
            box=skin["box_style"]
        )

    def render_control_panel(self) -> RenderableType:
        skin = self.get_skin()
        
        # Build layout control status
        state_text = "[bold green]ONLINE RUNNING[/]"
        if self.paused:
            state_text = "[bold yellow]AUTONOMY PAUSED[/]"
            
        bindings_table = Table.grid(expand=True, padding=1)
        bindings_table.add_column("Binding", style="cyan bold", width=12)
        bindings_table.add_column("Action", style="white")
        bindings_table.add_column("State Info", justify="right")
        
        bindings_table.add_row("[S]", "Toggle Themes / Color Skins", f"Skin: [bold magenta]{skin['name']}[/]")
        bindings_table.add_row("[P]", "Pause / Interrupt Swarm", f"Engine State: {state_text}")
        bindings_table.add_row("[R]", "Resume / Approve Next Task", "Arrow Batons: [green]Active[/]")
        bindings_table.add_row("[Q / Ctrl+C]", "Exit & Safe Handover to Background", "PID: [green]Worker Active[/]")
        
        return Panel(
            bindings_table,
            title=f"[{skin['title_color']}]█ Executive Control Panel [/{skin['title_color']}]",
            border_style=skin["border_color"],
            box=skin["box_style"]
        )

    def generate_layout(self) -> Layout:
        self.generate_simulated_activity()
        
        layout = Layout()
        layout.split_column(
            Layout(name="top", ratio=8),
            Layout(name="bottom", ratio=3)
        )
        
        layout["top"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=3)
        )
        
        layout["left"].update(self.render_htn_panel())
        layout["right"].update(self.render_logs_panel())
        layout["bottom"].update(self.render_control_panel())
        
        return layout

async def run_curses_tui_main(dry_run: bool = False):
    """
    Main loop using rich.live to draw the three viewports, supporting
    dynamic theme switching and keyboard interrupts gracefully.
    """
    dashboard = TerminalDashboard(dry_run=dry_run)
    console.print("[cyan]Initializing AJA Premium Live TUI...[/]")
    time.sleep(0.5)
    
    # We do a clean terminal print update loop using rich Live view
    with Live(dashboard.generate_layout(), refresh_per_second=4, screen=True) as live:
        try:
            # On Windows or headless terminals where curses key hooks might block,
            # we simulate an event-driven loop and support simple time/key loops.
            while dashboard.running:
                # Update layout
                live.update(dashboard.generate_layout())
                
                # Check for skin triggers / simple automation
                # (In full curses we capture keypresses, in rich.live we simulate or check stdin)
                await asyncio.sleep(0.25)
                
                # Simulate skin switching every few seconds in dry-run/demo to show off the visual aesthetic
                if dry_run and int(time.time()) % 15 == 0 and int(time.time() * 4) % 4 == 0:
                    dashboard.toggle_skin()
                    
        except KeyboardInterrupt:
            dashboard.running = False
            
    console.print("[bold green]TUI clean exit successful.[/]")

if __name__ == "__main__":
    asyncio.run(run_curses_tui_main(dry_run=True))
