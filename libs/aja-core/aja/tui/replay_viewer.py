import json
from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, ListView, ListItem, Label, TabbedContent, TabPane, Markdown, Log, Pretty, Static
from textual.containers import Horizontal, Vertical
from textual.binding import Binding

from aja.runtime.replay_engine import SessionReplayDataLoader


class ReplayApp(App):
    """
    AJA Execution Replay TUI
    Validates artifact architecture and provides execution lineage observability.
    """

    CSS = """
    #trace-container {
        height: 100%;
    }
    #timeline-list {
        width: 40%;
        height: 100%;
        border-right: solid $primary;
    }
    #event-detail {
        width: 60%;
        height: 100%;
        padding: 1;
        overflow-y: auto;
    }
    .event-line {
        padding: 0 1;
    }
    .event-stdout {
        color: $text;
    }
    .event-stderr {
        color: $error;
    }
    .event-lifecycle {
        color: $accent;
        text-style: bold;
    }
    #terminal-playback {
        height: 100%;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("j", "next_event", "Next Event", show=True),
        Binding("k", "prev_event", "Prev Event", show=True),
        Binding("p", "toggle_playback", "Toggle Live Playback", show=True),
        Binding("d", "show_diff", "View Diff", show=True),
    ]

    def __init__(self, session_id: str, executions_dir: Path):
        super().__init__()
        self.loader = SessionReplayDataLoader(session_id, executions_dir)
        self.events = self.loader.get_events()
        self.current_idx = 0
        self.playback_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        with TabbedContent(id="tabs"):
            with TabPane("Trace Explorer", id="tab-trace"):
                with Horizontal(id="trace-container"):
                    yield ListView(id="timeline-list")
                    with Vertical(id="event-detail"):
                        yield Static("Select an event to view trace details.", id="event-meta-title")
                        yield Pretty({}, id="event-meta-json")
            
            with TabPane("Terminal Playback", id="tab-terminal"):
                yield Log(id="terminal-playback", highlight=True)
                
            with TabPane("Workspace Diff", id="tab-diff"):
                yield Markdown(id="diff-markdown")
                
            with TabPane("Manifest", id="tab-manifest"):
                yield Pretty(self.loader.get_manifest() or {}, id="manifest-json")

        yield Footer()

    def on_mount(self) -> ComposeResult:
        self.title = f"AJA Replay Engine: {self.loader.session_id}"
        
        # Populate Timeline
        list_view = self.query_one("#timeline-list", ListView)
        for i, ev in enumerate(self.events):
            stream = ev.get("stream", "unknown")
            line = ev.get("line", "")
            time_str = ev.get("timestamp", "")[11:19]
            
            display_text = line.strip()
            if len(display_text) > 40:
                display_text = display_text[:37] + "..."
                
            label = Label(f"[{time_str}] {stream.upper()}: {display_text}", classes=f"event-line event-{stream}")
            list_view.append(ListItem(label, id=f"ev-{i}"))

        # Populate Diff
        diff_data = self.loader.get_diff()
        if diff_data and diff_data.get("diff_text"):
            diff_md = f"```diff\n{diff_data['diff_text']}\n```"
            self.query_one("#diff-markdown", Markdown).update(diff_md)
        else:
            self.query_one("#diff-markdown", Markdown).update("No workspace modifications detected in this session.")
            
        # Initial State
        if self.events:
            self.update_detail_panel(0)

    def on_list_view_highlighted(self, message: ListView.Highlighted):
        if message.item and message.item.id:
            idx_str = message.item.id.split("-")[1]
            self.update_detail_panel(int(idx_str))

    def update_detail_panel(self, idx: int):
        self.current_idx = idx
        ev = self.events[idx]
        
        # Update Trace Explorer Right Pane
        stream = ev.get("stream", "unknown")
        time_str = ev.get("timestamp", "")
        self.query_one("#event-meta-title", Static).update(f"Event: {stream.upper()} at {time_str}")
        self.query_one("#event-meta-json", Pretty).update(ev)
        
        # Synchronize Terminal Playback
        log_panel = self.query_one("#terminal-playback", Log)
        log_panel.clear()
        
        for i in range(idx + 1):
            e = self.events[i]
            s = e.get("stream")
            l = e.get("line", "").rstrip()
            if s == "stderr":
                log_panel.write(f"[red]{l}[/red]")
            elif s == "lifecycle":
                log_panel.write(f"[bold cyan]{l}[/bold cyan]")
            else:
                log_panel.write(l)

    def action_next_event(self):
        lv = self.query_one("#timeline-list", ListView)
        if lv.index is not None and lv.index < len(self.events) - 1:
            lv.index += 1

    def action_prev_event(self):
        lv = self.query_one("#timeline-list", ListView)
        if lv.index is not None and lv.index > 0:
            lv.index -= 1

    def action_show_diff(self):
        self.query_one("#tabs", TabbedContent).active = "tab-diff"

    def action_toggle_playback(self):
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = "tab-terminal"
        
        if self.playback_timer is not None:
            self.playback_timer.stop()
            self.playback_timer = None
            self.notify("Playback Paused")
        else:
            self.notify("Playback Started")
            self.playback_timer = self.set_interval(0.1, self._playback_tick)

    def _playback_tick(self):
        lv = self.query_one("#timeline-list", ListView)
        if lv.index is not None and lv.index < len(self.events) - 1:
            lv.index += 1
        else:
            if self.playback_timer:
                self.playback_timer.stop()
                self.playback_timer = None
            self.notify("Playback Finished")


def run_replay(session_id: str, executions_dir: Path):
    app = ReplayApp(session_id, executions_dir)
    app.run()
