"""
direct_session.py — Persistent Interactive Developer Session for AJA.
=====================================================================
Implements the `aja direct` interactive REPL that keeps a single
SwarmEngine worker alive across multiple user turns, maintaining:
  - A mutable session_history list shared across all turns (prompt-cache friendly)
  - LanceDB persistence via AJAMemory.mirror_chat_message()
  - Session resume from prior DB history (--resume flag)
  - slash meta-commands: /exit /clear /history /save /model
"""

import asyncio
import uuid
import json
import os
from pathlib import Path
from typing import List, Optional
import typer
from aja.orchestration.plan_gate import plan_gate

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from aja.config import PROJECT_ROOT, DATA_DIR
from aja.config import AJA_WORKER_MODEL
from aja.orchestration.swarm import SwarmEngine
from aja.memory.secretary import AJAMemory


# ---------------------------------------------------------------------------
# Session banner helpers
# ---------------------------------------------------------------------------

_BANNER = """\
[bold cyan]
 ╔══════════════════════════════════════════════════╗
 ║  AJA Direct — Interactive Developer Session      ║
 ║  Type your task, /help for commands, /exit to quit ║
 ╚══════════════════════════════════════════════════╝
[/bold cyan]"""


_SLASH_COMMANDS = [
    "/exit", "/quit",
    "/clear",
    "/context",
    "/history",
    "/save",
    "/model",
    "/help",
    "/status",
]


class DirectSession:
    """
    Persistent interactive developer session wrapper for AJA Direct Mode.

    Keeps a single SwarmEngine (worker model) alive across multiple user
    turns and accumulates conversation history for LLM prompt reuse
    (enabling provider-side prompt caching of the static system prefix).

    Usage:
        session = DirectSession(dry_run=False, resume=True)
        asyncio.run(session.run())
    """

    def __init__(
        self,
        dry_run: bool = False,
        model: Optional[str] = None,
        resume: bool = False,
        max_history: int = 40,
    ):
        # Resolve model: explicit CLI override → worker model from config → fallback
        resolved_model = model or AJA_WORKER_MODEL or "google:gemini-2.5-flash"

        self.engine = SwarmEngine(dry_run=dry_run, model=resolved_model)
        self.dry_run = dry_run
        self.max_history = max_history

        # Immutable system prompt for the entire session (enables provider caching)
        self.system_prompt: str = self.engine.presenter.direct_system_prompt

        # Mutable history list — shared with execute_direct across all turns
        self.session_history: List[dict] = []

        # Session identity for LanceDB mirroring
        self.session_id: str = uuid.uuid4().hex[:12]

        # Lazy-init AJAMemory (avoid heavy DB connection until first turn)
        self._memory = None

        if resume:
            self._load_history_from_db()

    # ------------------------------------------------------------------
    # Memory helpers
    # ------------------------------------------------------------------

    @property
    def memory(self):
        """Lazy-init AJAMemory to keep startup fast."""
        if self._memory is None:
            try:
                self._memory = AJAMemory()
            except Exception:
                self._memory = None
        return self._memory

    def _mirror(self, role: str, content: str) -> None:
        """Persist a turn to LanceDB (best-effort, non-blocking)."""
        if not content:
            return
        try:
            if self.memory:
                self.memory.mirror_chat_message(
                    role, content,
                    {"session_id": self.session_id, "mode": "direct"}
                )
        except Exception:
            pass  # Never let persistence failure break the interactive loop

    def _load_history_from_db(self) -> None:
        """
        Restore prior session turns from LanceDB aja_chat_history.
        Only loads turns that have mode=direct to avoid mixing with chat history.
        Falls back gracefully if LanceDB is unavailable.
        """
        from aja.interface.modern import console
        try:
            if self.memory:
                rows = self.memory.get_chat_history(limit=self.max_history)
                # Filter to direct-mode turns only
                direct_rows = [
                    r for r in rows
                    if isinstance(r.get("metadata"), dict)
                    and r["metadata"].get("mode") == "direct"
                ]
                if direct_rows:
                    for row in direct_rows:
                        self.session_history.append({
                            "role": row["role"],
                            "content": row["content"],
                        })
                    console.print(
                        f"[dim][Resume] Restored {len(direct_rows)} turn(s) from prior session.[/dim]"
                    )
                else:
                    console.print("[dim][Resume] No prior direct-mode history found. Starting fresh.[/dim]")
        except Exception as e:
            console.print(f"[yellow]⚠ Could not restore session history: {e}[/yellow]")

    # ------------------------------------------------------------------
    # Meta-command handlers
    # ------------------------------------------------------------------

    def _handle_meta(self, raw: str, console) -> bool:
        """
        Handle slash meta-commands. Returns True if command was handled
        (loop should continue), False if the session should exit.
        """
        parts = raw.strip().split(" ", 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            console.print("\n[bold cyan]AJA:[/] Farewell. Session closed. Standing by.")
            return False  # Signal exit

        elif cmd == "/clear":
            self.session_history.clear()
            console.clear()
            console.print(_BANNER)
            console.print("[dim]History cleared for this session.[/dim]")

        elif cmd == "/history":
            if not self.session_history:
                console.print("[dim]No history in this session yet.[/dim]")
            else:
                console.print(f"\n[bold cyan]Session History ({len(self.session_history)} turns):[/]")
                for i, msg in enumerate(self.session_history[-20:], 1):
                    role_label = "[bold green]User[/]" if msg["role"] == "user" else "[bold cyan]AJA[/]"
                    preview = str(msg.get("content", ""))[:120].replace("\n", " ")
                    console.print(f"  {i:02d}. {role_label}: {preview}")

        elif cmd == "/save":
            # Save session history to a JSON file
            save_path = DATA_DIR / f"sessions/direct_{self.session_id}.json"
            save_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                save_path.write_text(
                    json.dumps(self.session_history, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                console.print(f"[green]✔ Session saved to: {save_path}[/green]")
            except Exception as e:
                console.print(f"[red]✘ Save failed: {e}[/red]")

        elif cmd == "/model":
            from aja.config import AJA_WORKER_MODEL, AJA_PLANNER_MODEL
            console.print(f"\n[bold cyan]Current Direct Session Model:[/] {self.engine.model}")
            console.print(f"[dim]Provider: {self.engine.provider} | Worker default: {AJA_WORKER_MODEL}[/dim]\n")

        elif cmd == "/status":
            from aja.interface.modern import console as c
            console.print(
                f"\n[bold cyan]Direct Session Status[/]\n"
                f"  Session ID : [dim]{self.session_id}[/dim]\n"
                f"  Model      : {self.engine.provider}:{self.engine.model}\n"
                f"  Dry-run    : {self.dry_run}\n"
                f"  History    : {len(self.session_history)} turn(s)\n"
            )

        elif cmd == "/context":
            from aja.orchestration.context_window import (
                estimate_tokens, resolve_model_limit, MAX_TOOL_RESULT_CHARS
            )
            total = sum(estimate_tokens(str(m.get("content", ""))) for m in self.session_history)
            limit = resolve_model_limit(self.engine.model, self.engine.provider)
            pct = (total / limit * 100) if limit else 0
            console.print(
                f"\n[bold cyan]Context Budget:[/]\n"
                f"  Est. tokens used : [{'bold red' if pct > 80 else 'green'}]{total:,}[/] / {limit:,} "
                f"({'[bold red]' if pct > 80 else '[green]'}{pct:.1f}%[/])\n"
                f"  History turns    : {len(self.session_history)}\n"
                f"  Max tool result  : {MAX_TOOL_RESULT_CHARS:,} chars\n"
            )

        elif cmd == "/help":
            console.print(
                "\n[bold cyan]Direct Session Commands:[/]\n"
                "  [green]/clear[/]      — Wipe session history and redraw banner\n"
                "  [green]/context[/]    — Show estimated token budget usage\n"
                "  [green]/history[/]    — Show last 20 turns\n"
                "  [green]/save[/]       — Save session history to JSON file\n"
                "  [green]/model[/]      — Show current model info\n"
                "  [green]/status[/]     — Show session metadata\n"
                "  [green]/exit[/]       — End session\n"
            )
        else:
            console.print(f"[yellow]Unknown command: {cmd}. Type /help for options.[/yellow]")

        return True  # Continue session

    # ------------------------------------------------------------------
    # Core turn execution
    # ------------------------------------------------------------------

    async def _turn(self, objective: str, console, interactive: bool = True) -> None:
        """Execute one user turn: append to shared history, delegate to execute_direct, mirror to DB."""
        # Expand Zed-style dynamic context tokens (@file, @symbol, @diff, @diagnostics)
        from aja.orchestration.context_providers import expand_context_tokens
        objective = expand_context_tokens(objective)

        # Check if the user's input warrants an execution plan before executing
        if interactive:
            try:
                processed_objective = await plan_gate(objective)
            except typer.Exit:
                return
            except Exception as e:
                console.print(f"[dim]Plan gate check skipped: {e}[/dim]")
                processed_objective = objective
        else:
            processed_objective = objective

        # Append user message to shared history (execute_direct will see it)
        self.session_history.append({"role": "user", "content": processed_objective})
        self._mirror("user", processed_objective)

        # Recall injection: prepend recalled context as a system message for this turn
        recall_msg = None
        try:
            from aja.gateway.recall import hybrid_recall, format_recall_context

            sem, tmp = hybrid_recall(
                processed_objective,
                vector_memory=getattr(self.engine, "vector_memory", None),
                temporal_hours=24,
            )
            recall_context = format_recall_context(sem, tmp)
            if recall_context:
                recall_msg = {"role": "system", "content": recall_context}
                self.session_history.insert(0, recall_msg)
        except Exception:
            pass

        # Run the tool-calling loop — execute_direct mutates session_history in-place
        try:
            await self.engine.execute_direct(
                processed_objective,
                session_history=self.session_history,
                interactive=interactive,
            )
        except Exception as e:
            console.print(f"[bold red]✘ Session error: {e}[/bold red]")
        finally:
            if recall_msg is not None:
                try:
                    self.session_history.remove(recall_msg)
                except ValueError:
                    pass

        # Mirror last assistant turn(s) added by execute_direct
        for msg in reversed(self.session_history):
            if msg["role"] == "assistant":
                self._mirror("assistant", msg.get("content", ""))
                break

        # Compress history to fit within the model's token budget
        from aja.orchestration.context_window import compress_history
        compress_history(
            self.session_history,
            model=self.engine.model,
            provider=self.engine.provider,
        )

    # ------------------------------------------------------------------
    # Main REPL
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main interactive session loop."""
        from aja.interface.modern import console

        console.print(_BANNER)
        if self.dry_run:
            console.print("[bold yellow]⚠ DRY-RUN MODE — No live commands or mutations.[/bold yellow]\n")

        console.print(
            f"[dim]Model: {self.engine.provider}:{self.engine.model} | "
            f"Session: {self.session_id} | Type /help for commands[/dim]\n"
        )

        # Prompt session with file history for up-arrow recall
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        prompt_session = PromptSession(
            history=FileHistory(str(DATA_DIR / ".aja_direct_history")),
            completer=WordCompleter(_SLASH_COMMANDS, ignore_case=True),
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=kb,
            style=Style.from_dict({
                "bottom-toolbar": "#ffffff bg:#1a1a2e",
                "completion-menu.completion": "bg:#00aaaa #000000",
                "completion-menu.completion.current": "bg:#00ffff #000000",
            }),
        )

        def _toolbar():
            turns = len([m for m in self.session_history if m["role"] == "user"])
            mode = "DRY-RUN" if self.dry_run else "LIVE"
            return HTML(
                f' <style bg="ansicyan" fg="ansiblack"> <b>AJA Direct</b> </style> '
                f'| Model: {self.engine.provider}:{self.engine.model} '
                f'| Turns: {turns} '
                f'| Mode: {mode} '
            )

        while True:
            try:
                user_input = prompt_session.prompt(
                    HTML("<cyan><b>Direct > </b></cyan>"),
                    bottom_toolbar=_toolbar,
                ).strip()

                if not user_input:
                    continue

                if user_input.startswith("/"):
                    should_continue = self._handle_meta(user_input, console)
                    if not should_continue:
                        break
                    continue

                await self._turn(user_input, console)

            except KeyboardInterrupt:
                console.print("\n[dim](Ctrl+C — type /exit to quit)[/dim]")
                continue
            except EOFError:
                console.print(
                    "\n[bold cyan]AJA:[/] Pipe closed. Ending session gracefully."
                )
                break
            except Exception as e:
                console.print(f"[bold red]Session loop error: {e}[/bold red]")
                continue
