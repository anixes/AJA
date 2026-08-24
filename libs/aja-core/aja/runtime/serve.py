"""
AJA Serve Entrypoint
====================
Single-process composition of the three long-running AJA surfaces:

* Gateway adapters (Telegram/Discord/Slack)   -> aja.gateway.server.run_gateway
* Autonomy goals + heartbeats                 -> aja.runtime.autonomous_loop.main_loop
* Persisted cron scheduler tick loop          -> aja.scheduler.cron_scheduler.CronScheduler

All components share ONE process because the EventBus is an in-process
module singleton. A single stop event (fed by SIGTERM/SIGINT) tears every
child down gracefully.
"""

import asyncio
import logging
import signal
from pathlib import Path
from typing import Optional, Set

from aja.gateway.server import run_gateway
from aja.runtime.autonomous_loop import main_loop
from aja.scheduler.cron_scheduler import CronScheduler

logger = logging.getLogger(__name__)


def _install_signal_handlers(stop_event: asyncio.Event):
    """Route SIGTERM/SIGINT into ``stop_event``.

    Prefers ``loop.add_signal_handler`` (POSIX); falls back to
    ``signal.signal`` on Windows Proactor where add_signal_handler raises
    NotImplementedError. Both registrations are best-effort.

    Returns a cleanup callable that restores previous handlers.
    """
    loop = asyncio.get_running_loop()
    _restorers = []

    def _handle() -> None:
        logger.info("Shutdown signal received - stopping AJA serve...")
        stop_event.set()

    def _cleanup() -> None:
        for sig, previous in _restorers:
            try:
                loop.remove_signal_handler(sig)
                if previous is not None:
                    signal.signal(sig, previous)
            except (NotImplementedError, RuntimeError, ValueError, OSError):
                pass

    registered_any = False
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous = signal.getsignal(sig)
            loop.add_signal_handler(sig, _handle)
            _restorers.append((sig, previous))
            registered_any = True
        except (NotImplementedError, RuntimeError, AttributeError):
            try:
                previous = signal.getsignal(sig)
                signal.signal(sig, lambda *_: _handle())
                _restorers.append((sig, previous))
                registered_any = True
            except (ValueError, OSError):
                logger.debug("Could not register handler for %s", sig)
    if not registered_any:
        logger.warning("No signal handlers installed; rely on KeyboardInterrupt to stop.")
    return _cleanup


async def serve(host_stop_event: Optional[asyncio.Event] = None) -> None:
    """Run gateway + autonomy loop + cron scheduler until ``host_stop_event``
    is set (or a shutdown signal arrives).

    Cancellation propagates: whichever completes first (external stop event,
    signal-driven stop, or an unexpected child crash) tears down all others.
    """
    stop_event = host_stop_event if host_stop_event is not None else asyncio.Event()
    restore_signals = _install_signal_handlers(stop_event)

    gateway_task = asyncio.create_task(run_gateway(), name="aja-serve-gateway")
    autonomy_task = asyncio.create_task(main_loop(stop_event), name="aja-serve-autonomy")

    scheduler = CronScheduler()
    try:
        from aja.assistant import register_briefing_jobs

        register_briefing_jobs(scheduler)
    except Exception as exc:  # best-effort: missing calendar deps must not kill serving
        logger.warning("Briefing job registration skipped: %s", exc)
    scheduler.start()

    # File watcher service (optional — requires watchdog + config rules)
    file_watcher = None
    try:
        from aja.config import CONFIG

        fw_rules = getattr(getattr(CONFIG, "file_watchers", None), "rules", [])
        if fw_rules:
            from aja.core.conversation import ConversationCore
            from aja.llm import get_gateway_for_model
            from aja.orchestration.tools.executor import ToolExecutor
            from aja.orchestration.tools.native import NativeToolRegistry
            from aja.automation.file_watcher import FileWatcherService, FileWatcherRule

            gw, _ = get_gateway_for_model("copilot")
            core = ConversationCore(
                gateway=gw,
                tools_registry=NativeToolRegistry(),
                executor=ToolExecutor(),
            )
            rules = [
                FileWatcherRule(
                    path=r.path,
                    patterns=r.patterns,
                    goal=r.goal,
                    recursive=r.recursive,
                    debounce_seconds=r.debounce_seconds,
                    enabled=r.enabled,
                )
                for r in fw_rules
            ]
            file_watcher = FileWatcherService(rules=rules, core=core)
            file_watcher.start()
            logger.info("File watcher service started with %d rules.", len(rules))
    except Exception as exc:
        logger.warning("File watcher startup skipped: %s", exc)

    # Register workflow schedules from ~/.aja/workflows/
    try:
        import platformdirs

        workflows_dir = Path(
            platformdirs.user_data_dir("AJA", "Anixes")
        ) / "workflows"
        if workflows_dir.is_dir():
            from aja.automation.workflow import load_workflows
            from aja.automation.context import WorkflowContext

            workflows = load_workflows(workflows_dir)
            for wf in workflows:
                if wf.schedule:
                    scheduler.add_job(
                        goal=f"[workflow] {wf.name}: {wf.description}",
                        schedule_expr=wf.schedule,
                        workflow_name=wf.name,
                    )
                    logger.info("Registered workflow schedule: %s (%s)", wf.name, wf.schedule)
    except Exception as exc:
        logger.warning("Workflow schedule registration skipped: %s", exc)

    stop_waiter = asyncio.create_task(stop_event.wait(), name="aja-serve-stop-waiter")
    children: Set[asyncio.Task] = {gateway_task, autonomy_task}

    print("[*] AJA serving: gateway + scheduler + autonomy")
    try:
        await asyncio.wait(
            {stop_waiter, *children},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        restore_signals()
        stop_waiter.cancel()
        if file_watcher is not None:
            file_watcher.stop()
        for task in children:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*children, stop_waiter, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(
                r, (asyncio.CancelledError, KeyboardInterrupt)
            ):
                logger.error("Serve component failed during shutdown: %r", r)
        await scheduler.stop_async()
        print("[*] AJA serve stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
