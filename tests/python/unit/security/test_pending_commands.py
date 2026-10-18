"""Tests for per-command exec approvals (security/pending_commands.py).

Covers the contract from docs/plans/EXEC_APPROVALS_SPEC.md:
- create/get/resolve happy path with one-shot idempotency
- TTL expiry (fail-closed)
- journal kinds emitted (EXEC_REQUESTED/EXEC_APPROVED/EXEC_REJECTED)
- store sweep does not grow unbounded
"""

import pytest

from aja.security.pending_commands import (
    PendingCommandStore,
    reset_pending_command_store,
)


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_pending_command_store()
    yield
    reset_pending_command_store()


@pytest.mark.anyio
async def test_create_and_resolve_approve():
    store = PendingCommandStore(ttl_seconds=60)
    pc = store.create("rm -rf tmp/build", chat_id="100", user_id="42")

    assert pc.token and pc.command == "rm -rf tmp/build"
    assert not pc.expired()
    assert store.get(pc.token) is pc

    handled, msg = await store.resolve(pc.token, True, "42")
    assert handled and "approved" in msg.lower()
    assert pc.resolved and pc.approved is True


@pytest.mark.anyio
async def test_resolve_is_one_shot():
    store = PendingCommandStore()
    pc = store.create("shutdown /s", "100", "42")

    ok1, _ = await store.resolve(pc.token, False, "42")
    ok2, msg2 = await store.resolve(pc.token, True, "42")

    assert ok1 and not ok2
    assert "already" in msg2.lower()
    # first decision wins — a later approve cannot flip an earlier reject
    assert pc.approved is False


@pytest.mark.anyio
async def test_expiry_fails_closed():
    store = PendingCommandStore()
    pc = store.create("dangerous", "100", "42", ttl_seconds=0)

    import asyncio

    await asyncio.sleep(0.05)
    assert pc.expired()
    assert store.get(pc.token) is None, "expired entries must not resolve"

    handled, msg = await store.resolve(pc.token, True, "42")
    assert not handled
    assert "expire" in msg.lower()


@pytest.mark.anyio
async def test_unknown_token_returns_unhandled():
    store = PendingCommandStore()
    handled, msg = await store.resolve("nonexistent", True, "42")
    assert not handled
    assert msg


@pytest.mark.anyio
async def test_sweep_bounds_pending_dict():
    store = PendingCommandStore()
    for i in range(50):
        store.create(f"cmd-{i}", "100", "42", ttl_seconds=0)

    import asyncio

    await asyncio.sleep(0.05)
    # next create sweeps expired entries
    store.create("current", "100", "42")
    live = [pc for pc in store._pending.values() if not pc.expired()]
    assert len(live) == 1


def test_journal_rows_written(monkeypatch):
    rows = []

    class FakeMemory:
        def add_runtime_event(self, row):
            rows.append(row)

    import aja.memory.secretary as sec

    monkeypatch.setattr(sec, "get_aja_memory", lambda: FakeMemory(), raising=False)

    store = PendingCommandStore()
    pc = store.create("audited cmd", "100", "42")
    assert rows and rows[-1]["kind"] == "EXEC_REQUESTED"
    assert rows[-1]["command"] == "audited cmd"


@pytest.mark.anyio
async def test_callback_execok_approves_and_executes(monkeypatch):
    from aja.gateway.tg_client import TelegramAdapter
    from aja.security.pending_commands import get_pending_command_store
    import aja.config

    monkeypatch.setattr(aja.config, "TELEGRAM_ALLOWED_USER_ID", "42")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "42")

    store = get_pending_command_store()
    pc = store.create("echo test_execok_123", chat_id="100", user_id="42")

    adapter = TelegramAdapter({"token": "dummy:token"})

    class FakeQuery:
        data = f"execok_{pc.token}"
        from_user = type("User", (), {"id": 42})()
        edited_texts = []

        async def answer(self):
            pass

        async def edit_message_text(self, text, **kwargs):
            self.edited_texts.append(text)

    query = FakeQuery()
    update = type("Update", (), {"callback_query": query})()
    await adapter._handle_callback(update, None)

    assert any("Command Completed" in t for t in query.edited_texts)
    assert pc.resolved and pc.approved is True


@pytest.mark.anyio
async def test_callback_execno_rejects(monkeypatch):
    from aja.gateway.tg_client import TelegramAdapter
    from aja.security.pending_commands import get_pending_command_store
    import aja.config

    monkeypatch.setattr(aja.config, "TELEGRAM_ALLOWED_USER_ID", "42")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "42")

    store = get_pending_command_store()
    pc = store.create("echo test_execno", chat_id="100", user_id="42")

    adapter = TelegramAdapter({"token": "dummy:token"})

    class FakeQuery:
        data = f"execno_{pc.token}"
        from_user = type("User", (), {"id": 42})()
        edited_texts = []

        async def answer(self):
            pass

        async def edit_message_text(self, text, **kwargs):
            self.edited_texts.append(text)

    query = FakeQuery()
    update = type("Update", (), {"callback_query": query})()
    await adapter._handle_callback(update, None)

    assert any("rejected" in t.lower() for t in query.edited_texts)
    assert pc.resolved and pc.approved is False


@pytest.mark.anyio
async def test_callback_toctou_reclassify_deny(monkeypatch):
    from aja.gateway.tg_client import TelegramAdapter
    from aja.security.pending_commands import get_pending_command_store
    import aja.config
    import aja.security.command_guard as cg

    monkeypatch.setattr(aja.config, "TELEGRAM_ALLOWED_USER_ID", "42")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "42")

    store = get_pending_command_store()
    pc = store.create("rm -rf /", chat_id="100", user_id="42")

    adapter = TelegramAdapter({"token": "dummy:token"})

    class FakeQuery:
        data = f"execok_{pc.token}"
        from_user = type("User", (), {"id": 42})()
        edited_texts = []

        async def answer(self):
            pass

        async def edit_message_text(self, text, **kwargs):
            self.edited_texts.append(text)

    query = FakeQuery()
    update = type("Update", (), {"callback_query": query})()
    await adapter._handle_callback(update, None)

    # rm -rf / classifies as deny -> must be blocked even if approved button was tapped!
    assert any("DENIED" in t or "blocked" in t for t in query.edited_texts)

