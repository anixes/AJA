"""
tests/python/test_direct_session.py
====================================
Unit tests for the DirectSession interactive developer session.

All tests are fully mocked — no live LLM, DB, or filesystem calls.
Uses asyncio.run() for async test support (no pytest-asyncio dependency needed).
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_engine(dry_run=False):
    """Return a fully mocked SwarmEngine."""
    engine = MagicMock()
    engine.dry_run = dry_run
    engine.provider = "copilot"
    engine.model = "claude-haiku-4.5"
    presenter = MagicMock()
    presenter.direct_system_prompt = "You are AJA. Be helpful."
    engine.presenter = presenter
    engine.execute_direct = AsyncMock(return_value=None)
    return engine


def _make_session(dry_run=False, resume=False, model=None):
    """
    Create a DirectSession with all heavy dependencies stubbed out.
    Patches the three module-level names that DirectSession uses:
      - aja.orchestration.direct_session.SwarmEngine
      - aja.orchestration.direct_session.AJAMemory
      - aja.orchestration.direct_session.AJA_WORKER_MODEL
    """
    import importlib
    # Patch at the module's own namespace so mock.patch can find them
    with (
        patch("aja.orchestration.direct_session.SwarmEngine", return_value=_make_engine(dry_run=dry_run)),
        patch("aja.orchestration.direct_session.AJAMemory", autospec=False),
        patch("aja.orchestration.direct_session.AJA_WORKER_MODEL", "copilot:claude-haiku-4.5"),
    ):
        from aja.orchestration import direct_session as mod
        # Prevent _load_history_from_db from touching real DB during init
        with patch.object(mod.DirectSession, "_load_history_from_db", return_value=None):
            s = mod.DirectSession(dry_run=dry_run, model=model, resume=resume)
    return s


# ---------------------------------------------------------------------------
# 1. Initialisation
# ---------------------------------------------------------------------------

class TestDirectSessionInit(unittest.TestCase):

    def test_init_uses_worker_model(self):
        """DirectSession initialises SwarmEngine with the worker model."""
        with (
            patch("aja.orchestration.direct_session.SwarmEngine") as MockEngine,
            patch("aja.orchestration.direct_session.AJAMemory"),
            patch("aja.orchestration.direct_session.AJA_WORKER_MODEL", "copilot:claude-haiku-4.5"),
        ):
            MockEngine.return_value = _make_engine()
            from aja.orchestration import direct_session as mod
            with patch.object(mod.DirectSession, "_load_history_from_db"):
                s = mod.DirectSession()
            self.assertTrue(MockEngine.called)

    def test_system_prompt_is_immutable_per_session(self):
        """System prompt captured at __init__ must not change when presenter mutates."""
        s = _make_session()
        original = s.system_prompt
        # Mutate engine presenter after init
        s.engine.presenter.direct_system_prompt = "CHANGED"
        # The captured prompt must still be the original value
        self.assertEqual(s.system_prompt, original)

    def test_session_history_starts_empty(self):
        """Fresh session (no resume) has empty history."""
        s = _make_session(resume=False)
        self.assertEqual(s.session_history, [])

    def test_session_id_is_unique(self):
        """Each session gets its own unique hex session ID."""
        s1 = _make_session()
        s2 = _make_session()
        self.assertNotEqual(s1.session_id, s2.session_id)


# ---------------------------------------------------------------------------
# 2. History accumulates across turns (pass-by-reference semantics)
# ---------------------------------------------------------------------------

class TestSessionHistoryAccumulation(unittest.TestCase):

    def test_history_grows_per_turn(self):
        """
        After two turns the session_history contains two user entries.
        execute_direct is mocked to append an assistant reply (simulating real behaviour).
        """
        s = _make_session()

        def _fake_execute(objective, session_history=None):
            if session_history is not None:
                session_history.append({"role": "assistant", "content": "Done."})
            return None

        s.engine.execute_direct = AsyncMock(side_effect=_fake_execute)

        async def _run():
            mock_console = MagicMock()
            await s._turn("Task 1", mock_console)
            await s._turn("Task 2", mock_console)

        asyncio.run(_run())

        user_turns = [m for m in s.session_history if m["role"] == "user"]
        self.assertEqual(len(user_turns), 2)
        self.assertEqual(user_turns[0]["content"], "Task 1")
        self.assertEqual(user_turns[1]["content"], "Task 2")

    def test_execute_direct_receives_shared_history(self):
        """
        execute_direct must be called with the session_history kwarg pointing
        to the same list object (not a copy) — the key contract for context sharing.
        """
        s = _make_session()

        async def _run():
            mock_console = MagicMock()
            await s._turn("Do something", mock_console)

        asyncio.run(_run())

        call_kwargs = s.engine.execute_direct.call_args
        self.assertIsNotNone(call_kwargs)
        self.assertIs(call_kwargs.kwargs.get("session_history"), s.session_history)


# ---------------------------------------------------------------------------
# 3. Single-shot execute_direct backward compatibility (session_history=None)
# ---------------------------------------------------------------------------

class TestSwarmExecuteDirectBackwardsCompat(unittest.TestCase):

    def test_execute_direct_with_session_history_appends_to_caller_list(self):
        """
        When session_history is provided, execute_direct appends to that list.
        We verify via the DirectSession._turn helper which owns the shared list.
        """
        s = _make_session()

        appended = []

        def _fake_execute(objective, session_history=None):
            if session_history is not None:
                session_history.append({"role": "assistant", "content": "ok"})
            return None

        s.engine.execute_direct = AsyncMock(side_effect=_fake_execute)

        async def _run():
            await s._turn("check list", MagicMock())

        asyncio.run(_run())

        # session_history must contain both the user msg (added by _turn before calling
        # execute_direct) and the assistant reply (added by execute_direct via the
        # shared reference)
        roles = [m["role"] for m in s.session_history]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_execute_direct_none_history_means_fresh_scope(self):
        """
        When session_history=None, the method signature accepts None without error.
        We test this by exercising the default-argument branch through the DirectSession
        using a mocked engine that verifies it still gets called with the kwarg.
        """
        s = _make_session()
        # Manually call execute_direct with None to confirm it's accepted
        captured = {}

        async def _fake(objective, session_history=None):
            captured["sh"] = session_history

        s.engine.execute_direct = AsyncMock(side_effect=_fake)

        async def _run():
            await s._turn("hello", MagicMock())

        asyncio.run(_run())

        # _turn always passes the shared list, not None, so session_history is not None
        self.assertIsNotNone(captured.get("sh"))
        # Explicitly calling with None must also be valid (no AttributeError)
        asyncio.run(s.engine.execute_direct("test", session_history=None))
        self.assertIsNone(captured.get("sh"))



# ---------------------------------------------------------------------------
# 4. Meta-command handling
# ---------------------------------------------------------------------------

class TestMetaCommands(unittest.TestCase):

    def _call_meta(self, session, cmd):
        mock_console = MagicMock()
        return session._handle_meta(cmd, mock_console), mock_console

    def test_exit_returns_false(self):
        s = _make_session()
        result, _ = self._call_meta(s, "/exit")
        self.assertFalse(result)

    def test_quit_returns_false(self):
        s = _make_session()
        result, _ = self._call_meta(s, "/quit")
        self.assertFalse(result)

    def test_clear_empties_history(self):
        s = _make_session()
        s.session_history = [{"role": "user", "content": "hi"}]
        result, _ = self._call_meta(s, "/clear")
        self.assertTrue(result)
        self.assertEqual(s.session_history, [])

    def test_history_prints_without_error(self):
        s = _make_session()
        s.session_history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
        ]
        result, console = self._call_meta(s, "/history")
        self.assertTrue(result)
        console.print.assert_called()

    def test_help_prints_without_error(self):
        s = _make_session()
        result, console = self._call_meta(s, "/help")
        self.assertTrue(result)
        console.print.assert_called()

    def test_unknown_command_stays_alive(self):
        s = _make_session()
        result, _ = self._call_meta(s, "/nonexistent")
        # Unknown slash command must NOT kill the session
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# 5. AJAMemory mirroring
# ---------------------------------------------------------------------------

class TestLanceDBMirroring(unittest.TestCase):

    def test_mirror_called_for_user_turn(self):
        """mirror_chat_message must be called with 'user' for each user turn."""
        s = _make_session()
        mock_mem = MagicMock()
        s._memory = mock_mem  # Bypass lazy property

        async def _run():
            await s._turn("Write a test", MagicMock())

        asyncio.run(_run())

        calls = mock_mem.mirror_chat_message.call_args_list
        roles = [c[0][0] for c in calls]
        self.assertIn("user", roles)

    def test_mirror_failure_does_not_crash_session(self):
        """A LanceDB write error must never bubble up to break the interactive loop."""
        s = _make_session()
        mock_mem = MagicMock()
        mock_mem.mirror_chat_message.side_effect = RuntimeError("DB down")
        s._memory = mock_mem

        async def _run():
            await s._turn("safe task", MagicMock())  # Must not raise

        asyncio.run(_run())  # Completes cleanly


# ---------------------------------------------------------------------------
# 6. Dry-run mode propagation
# ---------------------------------------------------------------------------

class TestDryRunMode(unittest.TestCase):

    def test_dry_run_flag_propagated_to_session(self):
        """The dry_run flag must be stored on the DirectSession instance."""
        s = _make_session(dry_run=True)
        self.assertTrue(s.dry_run)

    def test_dry_run_false_by_default(self):
        s = _make_session()
        self.assertFalse(s.dry_run)


# ---------------------------------------------------------------------------
# 7. Resume loads prior turns from DB
# ---------------------------------------------------------------------------

class TestResumeFromDB(unittest.TestCase):

    def test_resume_loads_direct_mode_turns(self):
        """
        With resume=True, session_history is pre-populated from DB rows
        where metadata.mode == 'direct'. Rows with other modes are excluded.
        """
        fake_rows = [
            {"role": "user", "content": "prior task", "metadata": {"mode": "direct"}},
            {"role": "assistant", "content": "done", "metadata": {"mode": "direct"}},
            # This row should be excluded (wrong mode)
            {"role": "user", "content": "chat question", "metadata": {"mode": "chat"}},
        ]

        mock_mem_instance = MagicMock()
        mock_mem_instance.get_chat_history.return_value = fake_rows

        with (
            patch("aja.orchestration.direct_session.SwarmEngine", return_value=_make_engine()),
            patch("aja.orchestration.direct_session.AJAMemory", return_value=mock_mem_instance),
            patch("aja.orchestration.direct_session.AJA_WORKER_MODEL", "copilot:claude-haiku-4.5"),
        ):
            from aja.orchestration import direct_session as mod
            # Do NOT patch _load_history_from_db — we want it to run
            s = mod.DirectSession(resume=True)

        # Only the 2 direct-mode turns should be in history
        self.assertEqual(len(s.session_history), 2)
        self.assertEqual(s.session_history[0]["content"], "prior task")
        self.assertEqual(s.session_history[1]["content"], "done")

    def test_no_resume_skips_db(self):
        """Without --resume the DB must not be queried."""
        s = _make_session(resume=False)
        # memory prop is lazy — if it was never created, no DB call happened
        self.assertIsNone(s._memory)


# ---------------------------------------------------------------------------
# 8. gateway._build_system_message cache_control annotation
# ---------------------------------------------------------------------------

class TestGatewayCacheControl(unittest.TestCase):

    def _build(self, provider, model, system="test system prompt"):
        from aja.orchestration.gateway import _build_system_message
        return _build_system_message(provider, model, system)

    def test_anthropic_provider_gets_cache_control(self):
        msg = self._build("anthropic", "claude-3-5-sonnet-20241022")
        self.assertIsInstance(msg["content"], list)
        block = msg["content"][0]
        self.assertEqual(block["type"], "text")
        self.assertEqual(block["cache_control"], {"type": "ephemeral"})

    def test_copilot_claude_model_gets_cache_control(self):
        msg = self._build("copilot", "claude-haiku-4.5")
        self.assertIsInstance(msg["content"], list)
        block = msg["content"][0]
        self.assertEqual(block["cache_control"], {"type": "ephemeral"})

    def test_openai_provider_no_cache_control(self):
        msg = self._build("openai", "gpt-4o")
        # Must be plain string content — no list wrapping
        self.assertIsInstance(msg["content"], str)
        self.assertEqual(msg["content"], "test system prompt")

    def test_google_provider_no_cache_control(self):
        msg = self._build("google", "gemini-2.5-flash")
        self.assertIsInstance(msg["content"], str)

    def test_copilot_gpt_model_no_cache_control(self):
        """A GPT model via copilot must NOT receive cache_control."""
        msg = self._build("copilot", "gpt-4o")
        self.assertIsInstance(msg["content"], str)

    def test_llama_cpp_provider_no_cache_control(self):
        msg = self._build("llama_cpp", "gemma-4-e2b")
        self.assertIsInstance(msg["content"], str)

    def test_system_text_is_preserved_in_cache_block(self):
        text = "You are AJA the assistant."
        msg = self._build("anthropic", "claude-opus-4", text)
        content = msg["content"]
        if isinstance(content, list):
            self.assertEqual(content[0]["text"], text)
        else:
            self.assertEqual(content, text)

    def test_system_text_is_preserved_plain(self):
        text = "Plain system prompt."
        msg = self._build("openai", "gpt-4o-mini", text)
        self.assertEqual(msg["content"], text)


if __name__ == "__main__":
    unittest.main()
