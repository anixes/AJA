"""
Config-driven file watching service.

Watches directories for file changes matching glob patterns, then triggers
goals via ConversationCore. Debounced to avoid thrashing on rapid changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

    class FileSystemEventHandler:  # stub for type hints when unavailable
        pass

    class FileSystemEvent:  # stub
        src_path: str
        is_directory: bool


@dataclass
class FileWatcherRule:
    """Configuration for a single file watcher."""
    path: str
    patterns: List[str] = field(default_factory=lambda: ["*"])
    goal: str = ""
    recursive: bool = True
    debounce_seconds: float = 2.0
    enabled: bool = True


class _DebouncedHandler(FileSystemEventHandler):
    """Collects events per rule and fires once after the debounce window."""

    def __init__(self, rule: FileWatcherRule, service: "FileWatcherService"):
        super().__init__()
        self._rule = rule
        self._service = service
        self._pending: Dict[str, float] = {}  # path -> last event timestamp

    def _matches_pattern(self, filename: str) -> bool:
        from fnmatch import fnmatch

        return any(fnmatch(filename, p) or fnmatch(filename, f"*{p}") for p in self._rule.patterns)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if not self._matches_pattern(Path(str(event.src_path)).name):
            return
        now = time.monotonic()
        self._pending[str(event.src_path)] = now
        # Schedule a debounced fire
        asyncio.get_event_loop().call_later(
            self._rule.debounce_seconds,
            lambda: self._service._fire_if_stable(self._rule, dict(self._pending)),
        )


class FileWatcherService:
    """
    Config-driven file watching service that triggers goals on file changes.
    """

    def __init__(
        self,
        rules: Optional[List[FileWatcherRule]] = None,
        core: Any = None,
    ):
        self.rules = rules or []
        self._core = core
        self._observers: List[Any] = []
        self._handlers: List[_DebouncedHandler] = []
        self.is_running = False

    def start(self) -> None:
        if not WATCHDOG_AVAILABLE:
            logger.warning("[FileWatcher] watchdog not installed; file watching disabled.")
            return
        if not self.rules:
            return

        from watchdog.observers import Observer

        observer = Observer()
        for i, rule in enumerate(self.rules):
            if not rule.enabled:
                continue
            handler = _DebouncedHandler(rule, self)
            self._handlers.append(handler)
            observer.schedule(handler, rule.path, recursive=rule.recursive)
            logger.info(
                "[FileWatcher] Watching %s (patterns=%s, recursive=%s)",
                rule.path, rule.patterns, rule.recursive,
            )
        observer.start()
        self._observers.append(observer)
        self.is_running = True

    def stop(self) -> None:
        for obs in self._observers:
            obs.stop()
        for obs in self._observers:
            obs.join(timeout=5)
        self._observers.clear()
        self.is_running = False

    def _fire_if_stable(self, rule: FileWatcherRule, pending: Dict[str, float]) -> None:
        """Called after debounce window. Fires goal with changed files."""
        if not pending:
            return
        changed_files = "\n".join(sorted(pending.keys()))
        goal_text = rule.goal.replace("{changed_files}", changed_files)
        logger.info("[FileWatcher] Triggering goal: %s (%d files)", rule.goal[:80], len(pending))

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._process_goal(goal_text))
            else:
                loop.run_until_complete(self._process_goal(goal_text))
        except Exception as e:
            logger.error("[FileWatcher] Failed to process goal: %s", e)

    async def _process_goal(self, goal_text: str) -> None:
        if self._core is None:
            logger.warning("[FileWatcher] No ConversationCore configured.")
            return
        from aja.messaging.envelope import InboundMessage

        msg = InboundMessage(surface="file_watcher", chat_id="file_watcher", text=goal_text)
        async for _ in self._core.handle(msg):
            pass  # events consumed by adapters/renderers
