"""Night-shift Wave 2 E3 regression tests (approvals pipeline).

Covers:
1. ConversationCore.resolve_approval signature alignment with the shared
   gateway engine — reject must reach the right mission with approved=False.
2. Naive/unparseable approval expiry stamps never raise TypeError and are
   fail-closed.
3. Atomic claim: concurrent duplicate callbacks resolve side effects once,
   with rollback on transition failure.
5. DiscordEnvelope button resolution detaches the view (view=None).
6. InboundMessage image attachments yield a user-visible notice instead of
   being silently dropped.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import aja.gateway.approvals as approvals_mod
from aja.core.conversation import ConversationCore
from aja.gateway.approvals import resolve_approval
from aja.messaging.envelope import Attachment, InboundMessage


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeMemory:
    """Stands in for AJAMemory at the seam approvals.py actually touches."""

    def __init__(self, mission):
        self.mission = mission
        self.status_updates = []
        self.journal_events = []
        self.fail_table_add = False

    @property
    def db(self):
        return SimpleNamespace(open_table=lambda name: SimpleNamespace(add=self._add))

    def get_mission(self, mission_id):
        return dict(self.mission)

    def update_mission(self, mission_id, updates):
        self.status_updates.append((mission_id, dict(updates)))
        self.mission["status"] = updates.get("status", self.mission["status"])

    def _add(self, rows):  # called via asyncio.to_thread
        if self.fail_table_add:
            raise RuntimeError("journal write exploded")
        self.journal_events.extend(rows)


@pytest.fixture
def fake_memory(monkeypatch, pending_mission_factory):
    memory = FakeMemory(pending_mission_factory())
    monkeypatch.setattr(
        "aja.memory.secretary.get_aja_memory", lambda: memory
    )
    return memory


@pytest.fixture
def pending_mission_factory():
    def _make(status="PENDING", metadata=None):
        return {
            "mission_id": "M-E3TEST",
            "goal": "wave 2 fixture",
            "status": status,
            "metadata_json": json.dumps(metadata or {}),
        }

    return _make


def _reset_claim_locks():
    approvals_mod._mission_locks.clear()


# --------------------------------------------------------------------------- #
# 1. CRITICAL: ConversationCore.resolve_approval signature alignment
# --------------------------------------------------------------------------- #


class _SpyEngine:
    """Records how ConversationCore invokes the shared engine."""

    def __init__(self):
        self.calls = []
        self.memory = None

    async def __call__(self, platform="", user_id="", mission_id="", action="approve"):
        self.calls.append(
            {
                "platform": platform,
                "user_id": user_id,
                "mission_id": mission_id,
                "action": action,
            }
        )
        return True, f"resolved {mission_id} via {action}"


@pytest.mark.anyio
async def test_conversation_reject_reaches_right_mission(monkeypatch, fake_memory):
    """Regression: reject used to map approved→user_id and approver→mission_id,
    making reject unreachable and resolving the WRONG mission."""
    _reset_claim_locks()
    spy = _SpyEngine()
    monkeypatch.setattr(approvals_mod, "resolve_approval", spy)

    core = ConversationCore(
        gateway=SimpleNamespace(),
        tools_registry=SimpleNamespace(),
        executor=SimpleNamespace(),
        recall_enabled=False,
    )
    result = await core.resolve_approval("M-E3TEST", False, approver_id="user-42")

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["mission_id"] == "M-E3TEST"
    assert call["action"] == "reject"
    assert call["user_id"] == "user-42"
    assert result["handled"] is True


@pytest.mark.anyio
async def test_conversation_approve_maps_action_approve(monkeypatch, fake_memory):
    _reset_claim_locks()
    spy = _SpyEngine()
    monkeypatch.setattr(approvals_mod, "resolve_approval", spy)

    core = ConversationCore(
        gateway=SimpleNamespace(),
        tools_registry=SimpleNamespace(),
        executor=SimpleNamespace(),
        recall_enabled=False,
    )
    await core.resolve_approval("M-E3TEST", True, approver_id="user-7")

    assert spy.calls[0]["action"] == "approve"
    assert spy.calls[0]["mission_id"] == "M-E3TEST"


@pytest.mark.anyio
async def test_engine_reject_flips_status_and_journals(fake_memory):
    """End-to-end through the real engine: reject hits the right mission."""
    _reset_claim_locks()
    handled, message = await resolve_approval("cli", "user-42", "M-E3TEST", action="reject")

    assert handled is True
    assert "Rejected" in message
    assert fake_memory.status_updates == [("M-E3TEST", {"status": "REJECTED"})]
    kinds = [e["kind"] for e in fake_memory.journal_events]
    assert kinds == ["NODE_REJECTED"]
    # The journal row targets the actual mission, not the approver id.
    assert fake_memory.journal_events[0]["target"] == "M-E3TEST"


@pytest.mark.anyio
async def test_engine_approve_activates_mission(fake_memory):
    _reset_claim_locks()
    handled, message = await resolve_approval("cli", "user-42", "M-E3TEST", action="approve")

    assert handled is True
    assert fake_memory.mission["status"] == "ACTIVE"
    assert [e["kind"] for e in fake_memory.journal_events] == ["NODE_APPROVED"]


# --------------------------------------------------------------------------- #
# 2. HIGH: naive / unparseable expiry handling (fail-closed)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_naive_past_expiry_treated_as_expired(fake_memory, pending_mission_factory):
    _reset_claim_locks()
    fake_memory.mission = pending_mission_factory(
        metadata={"approval_expires_at": "2020-01-01T00:00:00"}  # no offset
    )
    handled, message = await resolve_approval("cli", "u", "M-E3TEST")
    assert handled is False
    assert "expired" in message.lower()


@pytest.mark.anyio
async def test_naive_future_expiry_still_resolvable(fake_memory, pending_mission_factory):
    _reset_claim_locks()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    fake_memory.mission = pending_mission_factory(metadata={"approval_expires_at": future})
    handled, _ = await resolve_approval("cli", "u", "M-E3TEST")
    assert handled is True


@pytest.mark.anyio
async def test_aware_past_expiry_expired(fake_memory, pending_mission_factory):
    _reset_claim_locks()
    fake_memory.mission = pending_mission_factory(
        metadata={"approval_expires_at": "2020-01-01T00:00:00Z"}
    )
    handled, _ = await resolve_approval("cli", "u", "M-E3TEST")
    assert handled is False


@pytest.mark.anyio
async def test_garbage_expiry_fails_closed_not_crash(fake_memory, pending_mission_factory):
    _reset_claim_locks()
    fake_memory.mission = pending_mission_factory(metadata={"expires_at": "not-a-date"})
    handled, message = await resolve_approval("cli", "u", "M-E3TEST")
    assert handled is False
    assert "expired" in message.lower()
    assert fake_memory.journal_events == []


@pytest.mark.anyio
async def test_none_expiry_expiry_is_not_a_date_object(fake_memory, pending_mission_factory):
    """A non-string garbage value (e.g. dict) also fails closed — no TypeError."""
    _reset_claim_locks()
    fake_memory.mission = pending_mission_factory(metadata={"expires_at": {"bad": 1}})
    handled, message = await resolve_approval("cli", "u", "M-E3TEST")
    assert handled is False


# --------------------------------------------------------------------------- #
# 3. HIGH: atomic claim — double resolution resolves exactly once
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_concurrent_double_approve_resolves_once(fake_memory):
    _reset_claim_locks()

    slow_update = FakeMemory.__dict__["update_mission"]

    def laggy_update(self, mission_id, updates):
        import time

        time.sleep(0.05)  # widen the race window for the second callback
        slow_update(self, mission_id, updates)

    fake_memory.update_mission = laggy_update.__get__(fake_memory, FakeMemory)

    results = await asyncio.gather(
        resolve_approval("discord", "u1", "M-E3TEST", action="approve"),
        resolve_approval("telegram", "u2", "M-E3TEST", action="approve"),
    )

    handled_flags = [r[0] for r in results]
    assert sum(handled_flags) == 1, f"expected exactly one apply, got {handled_flags}"
    transitions = [
        u for u in fake_memory.status_updates if u[1].get("status") == "ACTIVE"
    ]
    assert len(transitions) == 1
    assert [e["kind"] for e in fake_memory.journal_events] == ["NODE_APPROVED"]


@pytest.mark.anyio
async def test_sequential_second_click_reports_already_handled(fake_memory):
    _reset_claim_locks()
    first = await resolve_approval("cli", "u", "M-E3TEST", action="approve")
    second = await resolve_approval("cli", "u", "M-E3TEST", action="approve")

    assert first[0] is True
    assert second[0] is False
    assert "already handled" in second[1]
    assert len(fake_memory.journal_events) == 1


@pytest.mark.anyio
async def test_failed_journal_write_rolls_back_status(fake_memory):
    _reset_claim_locks()
    fake_memory.fail_table_add = True
    handled, message = await resolve_approval("cli", "u", "M-E3TEST", action="approve")

    assert handled is False
    assert fake_memory.mission["status"] == "PENDING", (
        "status must be rolled back to its pre-transition value on failure"
    )


# --------------------------------------------------------------------------- #
# 5. MEDIUM: DiscordEnvelope edits detach buttons after resolution
# --------------------------------------------------------------------------- #


def _make_interaction(user_id):
    edits = []

    async def edit_message(content=None, **kwargs):
        edits.append({"content": content, **kwargs})

    resp = SimpleNamespace(edit_message=edit_message)
    inter = SimpleNamespace(user=SimpleNamespace(id=user_id), response=resp, edits=edits)
    return inter


def _make_adapter():
    from aja.gateway.adapters import discord_envelope as de

    class ConcreteAdapter(de.DiscordEnvelopeAdapter):
        async def send_message(self, chat_id, text, **kwargs):
            return None

    return ConcreteAdapter({"token": "t"})


def test_resolution_edit_passes_view_none(monkeypatch):
    from aja.gateway.adapters import discord_envelope as de

    async def fake_resolve(platform, user_id, mission_id, action="approve"):
        return True, "✅ done"

    monkeypatch.setattr(de, "resolve_approval", fake_resolve)
    monkeypatch.setattr(de, "is_user_authorized", lambda platform, uid: True)

    adapter = _make_adapter()
    cb = adapter._make_button_callback("perm:reject:M-XYZ")
    inter = _make_interaction("999")
    asyncio.run(cb(inter))

    assert inter.edits == [{"content": "✅ done", "view": None}]


def test_unauthorized_edit_passes_view_none(monkeypatch):
    from aja.gateway.adapters import discord_envelope as de

    monkeypatch.setattr(de, "is_user_authorized", lambda platform, uid: False)

    adapter = _make_adapter()
    cb = adapter._make_button_callback("perm:approve:M-XYZ")
    inter = _make_interaction("999")
    asyncio.run(cb(inter))

    assert len(inter.edits) == 1
    assert inter.edits[0]["view"] is None


# --------------------------------------------------------------------------- #
# 6. MEDIUM: image attachments are not silently dropped
# --------------------------------------------------------------------------- #


def make_core():
    return ConversationCore(
        gateway=SimpleNamespace(),
        tools_registry=SimpleNamespace(),
        executor=SimpleNamespace(),
        recall_enabled=False,
    )


async def drain(core, message):
    return [ev async for ev in core.handle(message)]


@pytest.mark.anyio
async def test_image_attachment_yields_visible_notice():
    from aja.core.events import Final

    core = make_core()
    msg = InboundMessage(
        surface="repl",
        chat_id="c1",
        user_id="u1",
        text="what is this?",
        attachments=[
            Attachment(kind="image", url="file:///tmp/pic.png", mime="image/png", name="pic.png")
        ],
    )
    events = await drain(core, msg)

    finals = [e for e in events if type(e).__name__ == "Final"]
    assert finals, "must emit a user-visible event, never silence"
    assert "aren't processed" in finals[0].text
    assert "pic.png" in finals[0].text


@pytest.mark.anyio
async def test_image_attachment_does_not_hit_llm_pipeline():
    from aja.core.events import Delta, Error

    class BoomGateway:
        async def chat(self, **kw):
            raise AssertionError("LLM must not be invoked when images are present")

    core = ConversationCore(
        gateway=BoomGateway(),
        tools_registry=SimpleNamespace(),
        executor=SimpleNamespace(),
        recall_enabled=False,
    )
    msg = InboundMessage(
        surface="dashboard",
        chat_id="c1",
        user_id="u1",
        text="look",
        attachments=[Attachment(kind="image")],
    )
    events = await drain(core, msg)

    assert not [e for e in events if isinstance(e, (Delta, Error))]


class _OkGateway:
    def __init__(self):
        self.calls = []

    async def chat(self, **kw):
        self.calls.append(kw)
        return "hello"


class _OkRegistry:
    def get_schemas(self, interactive=True):
        return []


class _OkExecutor:
    async def dispatch_tool_calls(self, tool_calls=None, **kw):
        return [
            SimpleNamespace(tool=tc["tool"], success=True, data="ok")
            for tc in (tool_calls or [])
        ]


@pytest.mark.anyio
async def test_non_image_attachments_still_flow_through():
    """Only images trigger the notice; plain text messages behave as before."""
    from aja.core.events import Final

    core = ConversationCore(
        gateway=_OkGateway(),
        tools_registry=_OkRegistry(),
        executor=_OkExecutor(),
        recall_enabled=False,
        loop_overrides={
            "history_compressor": lambda history, **kw: None,
            "result_truncator": lambda raw: raw,
            "trace_id_fn": lambda: "",
        },
    )
    msg = InboundMessage(surface="cli", chat_id="c1", user_id="u1", text="hi there")
    events = await drain(core, msg)

    finals = [e for e in events if isinstance(e, Final)]
    assert finals
    assert "aren't processed" not in finals[-1].text
