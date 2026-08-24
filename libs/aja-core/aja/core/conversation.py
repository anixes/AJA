"""ConversationCore — THE single conversation brain.

One entry point (:meth:`ConversationCore.handle`) that consumes an
``InboundMessage`` from any surface and yields typed :data:`CoreEvent`s that
platform adapters render natively.

Pipeline (each stage a private method, executed in order):

    INTAKE -> CLASSIFY -> GROUND -> PLAN -> EXECUTE -> PERSIST

Import-time purity contract: module scope imports ONLY the standard library
plus ``aja.core.events`` / ``aja.messaging.envelope`` (both stdlib-pure).
Every AJA subsystem (recall, scheduler, approvals, direct_loop) is resolved
lazily inside methods or injected by the caller, so a full mocked turn runs
without importing lancedb / aja.config / aja.api.bridge.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Protocol, runtime_checkable

from aja.core.events import (
    CoreEvent,
    Delta,
    Error,
    Final,
    ToolFinished,
    ToolStarted,
)
from aja.messaging.envelope import InboundMessage

logger = logging.getLogger(__name__)

__all__ = [
    "SessionStore",
    "InMemorySessionStore",
    "IntentResult",
    "ConversationCore",
]

_TEMPORAL_KEYWORDS = (
    "yesterday",
    "earlier",
    "last week",
    "this morning",
    "recently",
)

_STATUS_KEYWORDS = {
    "status",
    "system status",
    "status report",
    "give live report",
    "live report",
    "health check",
}

_REMINDERS_LIST_PATTERNS = (
    "/reminders",
    "list reminders",
    "show reminders",
    "what are my reminders",
    "my reminders",
)

_MISSION_STARTS = (
    "run ",
    "execute ",
    "create ",
    "write ",
    "fix ",
    "build ",
    "deploy ",
    "install ",
    "delete ",
    "refactor ",
    "set up ",
    "setup ",
)

_TASK_PREFIX_RE = re.compile(r"^(remember to|add task|note that)\s*", re.IGNORECASE)
_TIME_TAIL_RE = re.compile(
    r"(\b(?:at|on)\s+\S+.*|\b(?:tomorrow|today|tonight)\b.*"
    r"|\bin\s+\d+\s+\w+\b.*|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b.*)$",
    re.IGNORECASE,
)


@runtime_checkable
class SessionStore(Protocol):
    """Persistence protocol for rolling session state."""

    async def load(self, chat_id: str) -> dict: ...

    async def save(self, chat_id: str, state: dict) -> None: ...


class InMemorySessionStore:
    """Default no-persistence store backed by a plain dict."""

    def __init__(self) -> None:
        self._states: Dict[str, dict] = {}

    async def load(self, chat_id: str) -> dict:
        if chat_id not in self._states:
            self._states[chat_id] = {"history": [], "tasks": []}
        # Deep-copy so concurrent turns never mutate a shared dict.
        return copy.deepcopy(self._states[chat_id])

    async def save(self, chat_id: str, state: dict) -> None:
        # Store a private snapshot; the caller keeps its own working copy.
        self._states[chat_id] = copy.deepcopy(state)


@dataclass
class IntentResult:
    type: str  # CHAT / MISSION / REMINDER / TASK_CAPTURE / REMINDERS_LIST / STATUS
    task: str = ""
    when_raw: str = ""
    mission_id: Optional[str] = None


def _maybe_await(value):
    if asyncio.iscoroutine(value) or hasattr(value, "__await__"):
        return value
    return _identity_coro(value)


async def _identity_coro(v):
    return v


class ConversationCore:
    def __init__(
        self,
        *,
        gateway,
        tools_registry,
        executor,
        sessions: Optional[SessionStore] = None,
        recall_enabled: bool = True,
        policy: Any = None,
        authorizer: Optional[Callable[..., Any]] = None,
        recall_fn: Optional[Callable[..., Any]] = None,
        reminder_creator: Optional[Callable[..., Any]] = None,
        reminders_lister: Optional[Callable[[], list]] = None,
        task_store: Any = None,
        mission_store: Any = None,
        status_provider: Optional[Callable[[], Any]] = None,
        max_history_turns: int = 15,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        loop_overrides: Optional[dict] = None,
    ) -> None:
        self._gateway = gateway
        self._tools_registry = tools_registry
        self._executor = executor
        self._sessions: SessionStore = sessions or InMemorySessionStore()
        self._recall_enabled = recall_enabled
        self._policy = policy
        self._authorizer = authorizer
        self._recall_fn = recall_fn
        self._reminder_creator = reminder_creator
        self._reminders_lister = reminders_lister
        self._task_store = task_store
        self._mission_store = mission_store
        self._status_provider = status_provider
        self._max_history_turns = max_history_turns
        self._system_prompt = system_prompt
        self._model = model
        self._provider = provider
        # Purity-preserving direct-loop injectables; callers may override via
        # loop_overrides (e.g. real compressors/trace fns in production wiring).
        self._loop_overrides: dict = {
            "history_compressor": lambda history, **kw: None,
            "result_truncator": lambda raw: raw[:4000],
            "trace_id_fn": lambda: "",
        }
        if loop_overrides:
            self._loop_overrides.update(loop_overrides)

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #

    async def handle(self, msg: InboundMessage) -> AsyncIterator[CoreEvent]:
        """Single entry point. Yields typed events (CoreEvent subclasses)."""
        text = (msg.text or "").strip()
        try:
            session = await self._stage_intake(msg)
        except PermissionError as e:
            yield Error(code="AUTH_DENIED", message=str(e), recoverable=False)
            return
        except Exception as e:
            yield Error(code="INTAKE_FAILED", message=str(e))
            return

        intent = self._stage_classify(session, text)
        try:
            if intent.type == "MISSION":
                intent.mission_id = await self._stage_plan(intent.type, text)
            messages = await self._stage_ground(session, text)
            final_text = ""
            async for ev in self._stage_execute(intent, session, messages):
                if isinstance(ev, Final):
                    final_text = ev.text
                yield ev
            if intent.type != "CHAT" and text:
                # Non-chat handlers don't mutate history via the direct loop;
                # record the exchange here so sessions stay replayable.
                session["history"].append({"role": "user", "content": text})
                if final_text:
                    session["history"].append({"role": "assistant", "content": final_text})
            # Ephemeral per-turn keys must never leak into the session store,
            # regardless of which intent handler ran.
            session.pop("_recall_block", None)
            session.pop("_working_history", None)
            await self._stage_persist(session, msg.chat_id)
        except Exception as e:
            logger.exception("ConversationCore pipeline failure")
            yield Error(code="PIPELINE_FAILED", message=f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ #
    # Stage 1: INTAKE
    # ------------------------------------------------------------------ #

    async def _stage_intake(self, msg: InboundMessage) -> dict:
        session = await self._sessions.load(msg.chat_id)
        session.setdefault("history", [])
        session.setdefault("tasks", [])
        session["surface"] = msg.surface
        session["user_id"] = msg.user_id
        session["_chat_id"] = msg.chat_id
        ok = True
        if self._authorizer is not None:
            ok = await _maybe_await(
                self._authorizer(msg.surface, msg.user_id, msg.chat_id)
            )
        elif self._policy is not None and hasattr(self._policy, "authorize"):
            ok = await _maybe_await(
                self._policy.authorize(msg.surface, msg.user_id, msg.chat_id)
            )
        if not ok:
            raise PermissionError(
                f"user {msg.user_id!r} is not authorized on {msg.surface}"
            )
        return session

    # ------------------------------------------------------------------ #
    # Stage 2: CLASSIFY
    # ------------------------------------------------------------------ #

    def _stage_classify(self, session: dict, text: str) -> IntentResult:
        low = text.lower().strip()

        if low.startswith("/mission ") or low == "/mission":
            return IntentResult(type="MISSION", task=text[len("/mission"):].strip())
        if any(low.startswith(p) or low == p.strip() for p in _REMINDERS_LIST_PATTERNS):
            return IntentResult(type="REMINDERS_LIST")

        if low.startswith("remind me") or low.startswith("reminder:"):
            body = re.sub(r"^(remind me to|remind me|reminder:)\s*", "", low, flags=re.IGNORECASE)
            m = _TIME_TAIL_RE.search(body)
            if m:
                when_raw = m.group(0).strip()
                task = body[: m.start()].strip(" ,") or body.strip()
                return IntentResult(type="REMINDER", task=task, when_raw=when_raw)
            return IntentResult(type="REMINDER", task=body, when_raw="")

        stripped_prefix = _TASK_PREFIX_RE.sub("", text, count=1)
        if stripped_prefix != text:
            return IntentResult(type="TASK_CAPTURE", task=stripped_prefix.strip())

        if low in _STATUS_KEYWORDS or any(p in low for p in ("live report", "is it started", "is it running")):
            return IntentResult(type="STATUS")
        if low.startswith("/status"):
            return IntentResult(type="STATUS")

        if any(low.startswith(s) for s in _MISSION_STARTS) and len(text.split()) >= 3:
            return IntentResult(type="MISSION", task=text)

        return IntentResult(type="CHAT", task=text)

    # ------------------------------------------------------------------ #
    # Stage 3: GROUND
    # ------------------------------------------------------------------ #

    async def _stage_ground(self, session: dict, text: str) -> List[Dict[str, str]]:
        history: List[Dict[str, str]] = session["history"]
        self._compress_history(history)

        messages: List[Dict[str, str]] = []
        if self._recall_enabled and self._recall_fn is not None:
            block = await self._recall_block(text)
            if block:
                # The recall block rides the system role into the LLM prompt
                # (run_direct_loop builds its prompt from session_history).
                session["_recall_block"] = block
                messages.append({"role": "system", "content": block})

        working_history = list(history)
        working_history.append({"role": "user", "content": text})
        messages.extend(working_history)
        # Stash for execute/persist so the direct loop mutates the same copy.
        session["_working_history"] = working_history
        return messages

    async def _recall_block(self, query: str) -> str:
        from aja.gateway.recall import format_recall_context

        semantic: list[dict] = []
        temporal: list[dict] = []
        try:
            semantic, temporal = self._split_recall_result(
                await self._invoke_recall(query)
            )
        except Exception as e:  # best-effort: recall must never kill the turn
            logger.debug("recall_fn failed: %s", e)
        if any(k in query.lower() for k in _TEMPORAL_KEYWORDS):
            try:
                from aja.gateway.recall import time_recall

                temporal = await asyncio.to_thread(time_recall, hours_back=48) or []
            except Exception as e:  # best-effort
                logger.debug("time_recall unavailable: %s", e)
        if not semantic and not temporal:
            return ""
        return format_recall_context(semantic, temporal)

    async def _invoke_recall(self, query: str):
        """Invokes the injected recall_fn without blocking the event loop.

        Async recall fns are awaited directly; sync ones (embedding/LanceDB
        work) run in a worker thread.
        """
        fn = self._recall_fn
        if asyncio.iscoroutinefunction(fn):
            return await fn(query)
        result = await asyncio.to_thread(fn, query)
        if asyncio.iscoroutine(result):  # pragma: no cover - defensive
            return await result
        return result

    @staticmethod
    def _split_recall_result(result) -> tuple:
        """Normalizes recall_fn output shapes.

        Supported contracts: a flat list of semantic entries (production
        recall_fn lambdas) or a ``(semantic, temporal)`` tuple of two lists.
        """
        if (
            isinstance(result, tuple)
            and len(result) == 2
            and all(isinstance(part, (list, tuple)) for part in result)
        ):
            return list(result[0] or []), list(result[1] or [])
        return list(result or []), []

    def _compress_history(self, history: List[Dict[str, str]]) -> None:
        max_msgs = self._max_history_turns * 2
        while len(history) > max_msgs:
            history.pop(0)

    # ------------------------------------------------------------------ #
    # Stage 4: PLAN
    # ------------------------------------------------------------------ #

    async def _stage_plan(self, intent_type: str, text: str) -> Optional[str]:
        if intent_type != "MISSION":
            return None
        if self._mission_store is not None and hasattr(self._mission_store, "create_mission"):
            mission = await _maybe_await(self._mission_store.create_mission(text))
            if isinstance(mission, dict):
                return mission.get("mission_id")
        return f"M-{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------ #
    # Stage 5: EXECUTE
    # ------------------------------------------------------------------ #

    async def _stage_execute(
        self, intent: IntentResult, session: dict, messages: List[Dict[str, str]]
    ) -> AsyncIterator[CoreEvent]:
        handler = {
            "CHAT": self._exec_chat,
            "REMINDER": self._exec_reminder,
            "TASK_CAPTURE": self._exec_task_capture,
            "REMINDERS_LIST": self._exec_reminders_list,
            "STATUS": self._exec_status,
            "MISSION": self._exec_mission,
        }.get(intent.type, self._exec_chat)
        async for ev in handler(intent, session, messages):
            yield ev

    async def _exec_chat(self, intent, session, messages):
        queue: asyncio.Queue = asyncio.Queue()

        def _wrap_executor(executor):
            orig_dispatch = executor.dispatch_tool_calls

            async def dispatch(tool_calls=None, **kw):
                calls = tool_calls or []
                for tc in calls:
                    args_summary = tc.get("arguments", "{}")
                    if not isinstance(args_summary, str):
                        args_summary = json.dumps(args_summary, default=str)[:200]
                    queue.put_nowait(ToolStarted(name=tc.get("tool", "?"), args_summary=args_summary[:200]))
                t0 = time.perf_counter()
                results = await orig_dispatch(tool_calls=calls, **kw)
                dur_ms = (time.perf_counter() - t0) * 1000.0
                for r in results or []:
                    queue.put_nowait(
                        ToolFinished(
                            name=getattr(r, "tool", "?"),
                            success=bool(getattr(r, "success", True)),
                            duration_ms=round(dur_ms / max(1, len(results or [])), 2),
                        )
                    )
                return results

            return SimpleNamespace(dispatch_tool_calls=dispatch, execute=getattr(executor, "execute", None))

        def _on_command(cmd, result):
            success = result.get("status") == "success" if isinstance(result, dict) else True
            queue.put_nowait(ToolStarted(name="shell", args_summary=cmd[:200]))
            queue.put_nowait(ToolFinished(name="shell", success=bool(success)))

        hooks = SimpleNamespace(on_command=_on_command, on_tool_result=None, on_synthesis=None)
        outcome: Dict[str, Any] = {}
        history = session["_working_history"]
        recall_block = session.get("_recall_block", "")
        system_prompt = self._system_prompt
        if recall_block:
            system_prompt = (
                f"{self._system_prompt}\n\n{recall_block}" if system_prompt else recall_block
            )

        async def runner():
            from aja.orchestration.direct_loop import run_direct_loop

            outcome["result"] = await run_direct_loop(
                intent.task,
                gateway=self._gateway,
                tools_registry=self._tools_registry,
                executor=_wrap_executor(self._executor),
                system_prompt=system_prompt,
                session_history=history,
                interactive=True,
                model=self._model,
                provider=self._provider,
                hooks=hooks,
                **self._loop_overrides,
            )

        task = asyncio.create_task(runner())
        while True:
            get_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({task, get_task}, return_when=asyncio.FIRST_COMPLETED)
            if get_task in done:
                yield get_task.result()
                continue
            get_task.cancel()  # loop finished with empty queue (at this instant)
            await asyncio.gather(get_task, return_exceptions=True)  # suppress CancelledError
            break
        await asyncio.gather(task, return_exceptions=True)
        # Drain events queued between the last wait and runner completion so
        # tool activity emitted just before Final is never silently dropped.
        while not queue.empty():
            yield queue.get_nowait()

        err = outcome.get("error")
        if err is None and task.exception():
            err = task.exception()
        if err is not None:
            yield Error(code="EXECUTE_FAILED", message=f"{type(err).__name__}: {err}")
            return

        res = outcome.get("result") or {}
        final_text = ""
        structured = res.get("result") if isinstance(res, dict) else None
        if isinstance(structured, dict):
            final_text = str(structured.get("answer") or structured.get("report") or json.dumps(structured)[:1500])
        elif structured:
            final_text = str(structured)
        else:
            for m in reversed(history):
                if m.get("role") == "assistant":
                    final_text = m.get("content", "")
                    break
        if final_text:
            yield Delta(text=final_text)
        session["history"].extend(history[len(session["history"]) :])
        yield Final(text=final_text, artifacts={"turns": res.get("turns"), "status": res.get("status")})

    async def _exec_reminder(self, intent, session, messages):
        job = None
        if intent.when_raw:
            creator = self._reminder_creator
            if creator is None:
                from aja.scheduler.cron_scheduler import create_reminder as creator
            job = await _maybe_await(
                creator(intent.task, when_raw=intent.when_raw, chat_id=session.get("_chat_id"))
            )
        if job:
            yield Final(text=f"⏰ Saved — I'll remind you at {job.get('run_at', 'the scheduled time')}.")
        else:
            yield Error(
                code="REMINDER_UNPARSED",
                message="Couldn't parse that time. Try: 'remind me to call mom tomorrow 3pm'.",
            )

    async def _exec_task_capture(self, intent, session, messages):
        task_entry = {
            "title": intent.task[:120],
            "context": intent.task,
            "owner": "assistant_capture",
            "status": "pending",
        }
        created = None
        if self._task_store is not None and hasattr(self._task_store, "create_task"):
            created = await _maybe_await(self._task_store.create_task(dict(task_entry)))
        session["tasks"].append(task_entry)
        label = (created or {}).get("title", intent.task) if isinstance(created, dict) else intent.task
        yield Final(text=f"📌 Saved — *{label[:100]}*", artifacts={"task": task_entry})

    async def _exec_reminders_list(self, intent, session, messages):
        lister = self._reminders_lister
        if lister is None:
            from aja.scheduler.cron_scheduler import CronScheduler

            def lister():
                return [
                    j
                    for j in CronScheduler().list_jobs()
                    if str(j.get("goal", "")).startswith("Reminder:")
                ]

        try:
            jobs = await _maybe_await(lister()) or []
        except Exception as e:
            yield Error(code="REMINDERS_LIST_FAILED", message=str(e))
            return
        if not jobs:
            yield Final(text="📭 No active reminders.")
            return
        lines = [f"⏰ **Reminders** ({len(jobs)}):"]
        for j in jobs[:15]:
            goal = str(j.get("goal", ""))[10:]
            lines.append(f"  - [{j.get('job_id')}] {goal} → {j.get('schedule_expr', '')}")
        yield Final(text="\n".join(lines))

    async def _exec_status(self, intent, session, messages):
        if self._status_provider is not None:
            try:
                info = await _maybe_await(self._status_provider())
            except Exception as e:
                yield Error(code="STATUS_FAILED", message=str(e))
                return
            report = info if isinstance(info, str) else "\n".join(
                f"{k}: {v}" for k, v in (info or {}).items()
            )
        else:
            report = (
                "📊 **AJA Status**\n\n"
                f"• Sessions tracked: 1\n"
                f"• History turns this session: {len(session['history']) // 2}\n"
                f"• Captured tasks: {len(session['tasks'])}"
            )
        yield Final(text=report)

    async def _exec_mission(self, intent, session, messages):
        mid = intent.mission_id or "M-?"
        yield Final(
            text=(
                f"🚀 Mission Accepted ({mid}). I'm deploying a worker to handle this: "
                f"'{intent.task}'. I'll live-report progress here."
            ),
            artifacts={"mission_id": mid},
        )

    # ------------------------------------------------------------------ #
    # Stage 6: PERSIST + APPROVE hook
    # ------------------------------------------------------------------ #

    async def _stage_persist(self, session: dict, chat_id: str) -> None:
        session.pop("_chat_id", None)
        await self._sessions.save(chat_id, session)

    async def resolve_approval(self, approval_id: str, approved: bool, approver_id: str = "") -> dict:
        """APPROVE stage: delegate to the shared gateway approval engine."""
        from aja.gateway.approvals import resolve_approval

        return await _maybe_await(resolve_approval(approval_id, approved, approver_id))
