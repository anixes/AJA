"""Recall wiring tests: semantic/temporal recall injected as a system message
before LLM prompt assembly in both the gateway orchestrator and CLI DirectSession."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aja.gateway.base import MessageEvent, MessageType


SEM_RESULTS = [{"role": "assistant", "content": "past answer", "timestamp": "", "score": 0.9}]
TMP_RESULTS = [{"event_type": "MISSION_COMPLETED", "timestamp": "t", "summary": "did a thing"}]
RECALL_BLOCK = "## Previously discussed\n- past answer"


def _make_gateway():
    from aja.gateway.orchestrator import UnifiedGateway

    gw = UnifiedGateway()
    gw.telegram_adapter = AsyncMock()
    gw.telegram_adapter.send_message = AsyncMock()
    gw.aja_memory = MagicMock()
    gw.vector_memory = MagicMock()
    gw.gateway_state = MagicMock()
    gw.gateway_state.get_session.return_value = {"history": []}
    return gw


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(
        platform="telegram",
        chat_id="recall-chat",
        user_id="user1",
        text=text,
        message_type=MessageType.TEXT,
        raw_event=None,
    )


def test_orchestrator_injects_recall_system_message():
    async def scenario():
        gw = _make_gateway()
        captured = {}

        async def fake_chat(user_input, chat_history=None, image_url=None):
            captured["history"] = chat_history
            return "ok"

        gw.chat = fake_chat

        with patch("aja.gateway.recall.semantic_recall", return_value=SEM_RESULTS) as m_sem, \
             patch("aja.gateway.recall.time_recall", return_value=[]) as m_tmp, \
             patch("aja.gateway.recall.format_recall_context", return_value=RECALL_BLOCK), \
             patch.object(gw, "_is_telegram_user_authorized", return_value=True), \
             patch.object(gw, "route_intent", new=AsyncMock(return_value="CHAT")):
            await gw.handle_gateway_event(_make_event("hello there friend"))

        m_sem.assert_called_once()
        assert m_sem.call_args[0][0] == "hello there friend"
        assert m_sem.call_args[1]["vector_memory"] is gw.vector_memory
        m_tmp.assert_not_called()  # no time keyword
        history = captured["history"]
        assert history[0] == {"role": "system", "content": RECALL_BLOCK}
        assert history[-1]["role"] == "user"
        assert "hello there friend" in str(history[-1])
    asyncio.run(scenario())


def test_orchestrator_empty_recall_no_injection():
    async def scenario():
        gw = _make_gateway()
        captured = {}

        async def fake_chat(user_input, chat_history=None, image_url=None):
            captured["history"] = chat_history
            return "ok"

        gw.chat = fake_chat

        with patch("aja.gateway.recall.semantic_recall", return_value=[]), \
             patch("aja.gateway.recall.time_recall", return_value=[]), \
             patch("aja.gateway.recall.format_recall_context", return_value=""), \
             patch.object(gw, "_is_telegram_user_authorized", return_value=True), \
             patch.object(gw, "route_intent", new=AsyncMock(return_value="CHAT")):
            await gw.handle_gateway_event(_make_event("hello there friend"))

        history = captured["history"]
        assert all(m.get("role") != "system" for m in history)
        assert any(m["role"] == "user" and "hello there friend" in str(m) for m in history)
    asyncio.run(scenario())


def test_orchestrator_time_keyword_triggers_temporal_recall():
    async def scenario():
        gw = _make_gateway()
        captured = {}

        async def fake_chat(user_input, chat_history=None, image_url=None):
            captured["history"] = chat_history
            return "ok"

        gw.chat = fake_chat

        with patch("aja.gateway.recall.semantic_recall", return_value=[]), \
             patch("aja.gateway.recall.time_recall", return_value=TMP_RESULTS) as m_tmp, \
             patch("aja.gateway.recall.format_recall_context", return_value=RECALL_BLOCK) as m_fmt, \
             patch.object(gw, "_is_telegram_user_authorized", return_value=True), \
             patch.object(gw, "route_intent", new=AsyncMock(return_value="CHAT")):
            await gw.handle_gateway_event(_make_event("what happened earlier today friend"))

        m_tmp.assert_called_once_with(24)
        assert m_fmt.call_args[0][1] == TMP_RESULTS
        assert captured["history"][0]["role"] == "system"
    asyncio.run(scenario())


@pytest.mark.parametrize("keyword", ["yesterday", "earlier", "last week"])
def test_all_time_keywords_trigger_temporal(keyword):
    async def scenario():
        gw = _make_gateway()

        async def fake_chat(user_input, chat_history=None, image_url=None):
            return "ok"

        gw.chat = fake_chat

        with patch("aja.gateway.recall.semantic_recall", return_value=[]), \
             patch("aja.gateway.recall.time_recall", return_value=[]) as m_tmp, \
             patch("aja.gateway.recall.format_recall_context", return_value=""), \
             patch.object(gw, "_is_telegram_user_authorized", return_value=True), \
             patch.object(gw, "route_intent", new=AsyncMock(return_value="CHAT")):
            await gw.handle_gateway_event(_make_event(f"tell me about {keyword} please"))

        assert m_tmp.called
    asyncio.run(scenario())


def test_direct_session_injects_and_cleans_up():
    async def scenario():
        from aja.orchestration.direct_session import DirectSession

        session = DirectSession.__new__(DirectSession)
        session.engine = MagicMock()
        session.engine.model = "test-model"
        session.engine.provider = "mock"
        session.engine.execute_direct = AsyncMock()
        seen = {}

        async def snapshot(objective, session_history=None, interactive=True):
            seen["history"] = list(session_history)

        session.engine.execute_direct.side_effect = snapshot
        session.dry_run = True
        session.max_history = 40
        session.session_history = []
        session.session_id = "testsession01"
        session._memory = MagicMock()
        console = MagicMock()

        with patch("aja.gateway.recall.semantic_recall", return_value=SEM_RESULTS), \
             patch("aja.gateway.recall.time_recall", return_value=[]) as m_tmp, \
             patch("aja.gateway.recall.format_recall_context", return_value=RECALL_BLOCK):
            await session._turn("what did we do earlier today", console=console, interactive=False)

        m_tmp.assert_called_once_with(24)
        # execute_direct saw the recall message prepended
        passed_history = seen["history"]
        assert passed_history[0] == {"role": "system", "content": RECALL_BLOCK}
        assert passed_history[1] == {"role": "user", "content": "what did we do earlier today"}
        # Recall block is ephemeral — removed after the turn
        assert all(m.get("role") != "system" for m in session.session_history)
    asyncio.run(scenario())


def test_direct_session_empty_recall_no_injection():
    async def scenario():
        from aja.orchestration.direct_session import DirectSession

        session = DirectSession.__new__(DirectSession)
        session.engine = MagicMock()
        session.engine.model = "test-model"
        session.engine.provider = "mock"
        session.engine.execute_direct = AsyncMock()
        session.dry_run = True
        session.max_history = 40
        session.session_history = []
        session.session_id = "testsession02"
        session._memory = MagicMock()
        console = MagicMock()

        with patch("aja.gateway.recall.semantic_recall", return_value=[]), \
             patch("aja.gateway.recall.time_recall", return_value=[]), \
             patch("aja.gateway.recall.format_recall_context", return_value=""):
            await session._turn("hello there", console=console, interactive=False)

        passed_history = session.engine.execute_direct.call_args[1]["session_history"]
        assert all(m.get("role") != "system" for m in passed_history)
        assert session.session_history == [{"role": "user", "content": "hello there"}]
    asyncio.run(scenario())


def test_direct_session_recall_failure_is_silent():
    async def scenario():
        from aja.orchestration.direct_session import DirectSession

        session = DirectSession.__new__(DirectSession)
        session.engine = MagicMock()
        session.engine.model = "test-model"
        session.engine.provider = "mock"
        session.engine.execute_direct = AsyncMock()
        session.dry_run = True
        session.max_history = 40
        session.session_history = []
        session.session_id = "testsession03"
        session._memory = MagicMock()
        console = MagicMock()

        with patch("aja.gateway.recall.semantic_recall", side_effect=RuntimeError("boom")):
            await session._turn("plain task", console=console, interactive=False)

        session.engine.execute_direct.assert_awaited_once()
        passed_history = session.engine.execute_direct.call_args[1]["session_history"]
        assert all(m.get("role") != "system" for m in passed_history)
    asyncio.run(scenario())
