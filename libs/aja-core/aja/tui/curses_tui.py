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

from aja.mcp import get_catalog, install_mcp_server

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

import threading
import sys

class AsyncKeyboardInput:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.loop = asyncio.get_event_loop()
        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self):
        if sys.platform == "win32":
            import msvcrt
            while self.running:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b'\x00', b'\xe0'):  # arrow keys prefix
                        ch2 = msvcrt.getch()
                        if ch2 == b'H':
                            key = "up"
                        elif ch2 == b'P':
                            key = "down"
                        else:
                            key = None
                    else:
                        try:
                            key = ch.decode("utf-8")
                        except UnicodeDecodeError:
                            key = None
                    if key:
                        self.loop.call_soon_threadsafe(self.queue.put_nowait, key)
                time.sleep(0.05)
        else:
            import select
            import tty
            import termios
            fd = sys.stdin.fileno()
            try:
                old_settings = termios.tcgetattr(fd)
            except Exception:
                # Fallback if stdin is not a TTY or non-interactive
                return
            try:
                tty.setraw(sys.stdin.fileno())
                while self.running:
                    rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if rlist:
                        ch = sys.stdin.read(1)
                        if ch == '\x1b':  # escape sequence
                            rlist2, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if rlist2:
                                ch2 = sys.stdin.read(1)
                                if ch2 == '[':
                                    ch3 = sys.stdin.read(1)
                                    if ch3 == 'A':
                                        key = "up"
                                    elif ch3 == 'B':
                                        key = "down"
                                    else:
                                        key = None
                                else:
                                    key = None
                            else:
                                key = "escape"
                        else:
                            key = ch
                        if key:
                            self.loop.call_soon_threadsafe(self.queue.put_nowait, key)
            except Exception:
                pass
            finally:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass

    def stop(self):
        self.running = False


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
        
        # Initialize MCP Catalog
        self.mcp_catalog = get_catalog()
        self.selected_mcp_index = 0

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

    def render_mcp_hub_panel(self) -> RenderableType:
        skin = self.get_skin()
        from aja.config import CONFIG
        
        installed_ids = {s.server_id.lower() for s in getattr(CONFIG, "mcp_servers", [])}
        
        table = Table.grid(expand=True, padding=0)
        table.add_column(" ", width=3)
        table.add_column("Server", justify="left", width=12)
        table.add_column("Status", justify="left", width=12)
        table.add_column("Description", justify="left")
        
        catalog_items = list(self.mcp_catalog.items())
        if self.selected_mcp_index >= len(catalog_items):
            self.selected_mcp_index = max(0, len(catalog_items) - 1)
            
        for idx, (name, info) in enumerate(catalog_items):
            is_selected = (idx == self.selected_mcp_index)
            is_installed = name in installed_ids
            
            status_text = "[bold green]INSTALLED[/]" if is_installed else "[dim white]AVAILABLE[/]"
            
            if is_selected:
                marker = f"[{skin['title_color']}]▶[/{skin['title_color']}]"
                name_style = f"bold {skin['accent_color']}"
                desc_style = "bold white"
            else:
                marker = " "
                name_style = "dim white"
                desc_style = "dim white"
                
            table.add_row(
                marker,
                Text(name, style=name_style),
                status_text,
                Text(info["description"], style=desc_style)
            )
            table.add_row("", "", "", "")
            
        return Panel(
            Align.left(table),
            title=f"[{skin['title_color']}]█ MCP Hub [/{skin['title_color']}]",
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
        try:
            from aja.runtime.execution import get_default_execution_manager
            active_executions = get_default_execution_manager().list_active()
            exec_state = f"{len(active_executions)} active"
        except Exception:
            exec_state = "unavailable"
        
        state_text = "[bold green]ONLINE RUNNING[/]"
        if self.paused:
            state_text = "[bold yellow]AUTONOMY PAUSED[/]"
            
        bindings_table = Table.grid(expand=True, padding=1)
        bindings_table.add_column("Binding", style="cyan bold", width=16)
        bindings_table.add_column("Action", style="white")
        bindings_table.add_column("State Info", justify="right")
        
        bindings_table.add_row("[T]", "Toggle Themes / Color Skins", f"Skin: [bold magenta]{skin['name']}[/]")
        bindings_table.add_row("[P]", "Pause / Interrupt Swarm", f"Engine State: {state_text}")
        bindings_table.add_row("[W / S or Arrow]", "Navigate MCP Catalog", f"Selected: [yellow]{list(self.mcp_catalog.keys())[self.selected_mcp_index]}[/]")
        bindings_table.add_row("[I]", "Install Selected MCP Server", f"Executions: [green]{exec_state}[/]")
        bindings_table.add_row("[R]", "Refresh MCP Catalog", "")
        bindings_table.add_row("[Q / Ctrl+C]", "Exit Dashboard", "PID: [green]Worker Active[/]")
        
        return Panel(
            bindings_table,
            title=f"[{skin['title_color']}]█ Executive Control Panel [/{skin['title_color']}]",
            border_style=skin["border_color"],
            box=skin["box_style"]
        )

    def handle_keypress(self, key: str):
        key_lower = key.lower()
        if key_lower == 't':
            self.toggle_skin()
        elif key_lower == 'p':
            self.paused = not self.paused
        elif key_lower == 'q':
            self.running = False
        elif key_lower in ('up', 'w'):
            self.selected_mcp_index = max(0, self.selected_mcp_index - 1)
        elif key_lower in ('down', 's'):
            catalog_items = list(self.mcp_catalog.items())
            self.selected_mcp_index = min(len(catalog_items) - 1, self.selected_mcp_index + 1)
        elif key_lower == 'i':
            self.install_selected_mcp()
        elif key_lower == 'r':
            self.refresh_mcp_catalog()

    def install_selected_mcp(self):
        catalog_items = list(self.mcp_catalog.items())
        if not catalog_items:
            return
        server_name, info = catalog_items[self.selected_mcp_index]
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] INFO [MCP Hub] Installing server '{server_name}'...")
        try:
            install_mcp_server(server_name)
            
            # Reload config object in config module dynamically
            from aja.config import load_and_validate_config
            import aja.config
            aja.config.CONFIG = load_and_validate_config()
            
            self.logs.append(f"[{time.strftime('%H:%M:%S')}] SUCCESS [MCP Hub] Server '{server_name}' successfully installed.")
        except Exception as e:
            self.logs.append(f"[{time.strftime('%H:%M:%S')}] ERROR [MCP Hub] Failed to install '{server_name}': {e}")

    def refresh_mcp_catalog(self):
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] INFO [MCP Hub] Refreshing MCP Catalog...")
        self.mcp_catalog = get_catalog()
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] SUCCESS [MCP Hub] Catalog refreshed.")

    def generate_layout(self) -> Layout:
        self.generate_simulated_activity()
        
        layout = Layout()
        layout.split_column(
            Layout(name="top", ratio=8),
            Layout(name="bottom", ratio=3)
        )
        
        layout["top"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="middle", ratio=3),
            Layout(name="right", ratio=3)
        )
        
        layout["left"].update(self.render_htn_panel())
        layout["middle"].update(self.render_mcp_hub_panel())
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
    
    keyboard = AsyncKeyboardInput()
    
    with Live(dashboard.generate_layout(), refresh_per_second=4, screen=True) as live:
        try:
            while dashboard.running:
                # Process any pending keys
                while not keyboard.queue.empty():
                    key = keyboard.queue.get_nowait()
                    dashboard.handle_keypress(key)
                    
                live.update(dashboard.generate_layout())
                await asyncio.sleep(0.1)
                
                # Simulate skin switching in dry-run
                if dry_run and int(time.time()) % 15 == 0 and int(time.time() * 10) % 10 == 0:
                    dashboard.toggle_skin()
        except KeyboardInterrupt:
            pass
        finally:
            dashboard.running = False
            keyboard.stop()
            
    console.print("[bold green]TUI clean exit successful.[/]")


if __name__ == "__main__":
    asyncio.run(run_curses_tui_main(dry_run=True))
