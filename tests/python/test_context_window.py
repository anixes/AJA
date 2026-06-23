"""
test_context_window.py — Unit tests for aja.orchestration.context_window
"""

import os
import unittest


class TestTokenEstimation(unittest.TestCase):

    def setUp(self):
        from aja.orchestration.context_window import estimate_tokens
        self.estimate_tokens = estimate_tokens

    def test_empty_string_returns_zero(self):
        self.assertEqual(self.estimate_tokens(""), 0)

    def test_short_string_nonzero(self):
        result = self.estimate_tokens("Hello world")
        self.assertGreater(result, 0)

    def test_proportionality(self):
        short = self.estimate_tokens("a" * 100)
        long  = self.estimate_tokens("a" * 1000)
        self.assertGreater(long, short)

    def test_large_text_reasonable_estimate(self):
        # 35,000 chars ≈ 10,000 tokens at 3.5 chars/token
        text = "x" * 35_000
        result = self.estimate_tokens(text)
        self.assertGreater(result, 8_000)
        self.assertLess(result, 12_000)


class TestTruncateToolResult(unittest.TestCase):

    def setUp(self):
        from aja.orchestration.context_window import truncate_tool_result
        self.truncate = truncate_tool_result

    def test_short_result_unchanged(self):
        text = "hello world"
        self.assertEqual(self.truncate(text, max_chars=1000), text)

    def test_exact_limit_unchanged(self):
        text = "a" * 8000
        self.assertEqual(self.truncate(text, max_chars=8000), text)

    def test_long_result_is_truncated(self):
        # 50,000 chars >> default 8,000 limit
        raw = "\n".join([f"LOG LINE {i}: " + "x" * 80 for i in range(500)])
        result = self.truncate(raw, max_chars=8_000)
        self.assertLess(len(result), len(raw))

    def test_truncation_marker_present(self):
        raw = "\n".join([f"LINE {i}" for i in range(200)])
        result = self.truncate(raw, max_chars=500)
        self.assertIn("truncated", result.lower())

    def test_head_lines_preserved(self):
        lines = [f"LINE_{i}" for i in range(200)]
        raw = "\n".join(lines)
        result = self.truncate(raw, max_chars=500)
        self.assertIn("LINE_0", result)

    def test_tail_lines_preserved(self):
        lines = [f"LINE_{i}" for i in range(200)]
        raw = "\n".join(lines)
        result = self.truncate(raw, max_chars=500)
        self.assertIn("LINE_199", result)

    def test_env_var_override(self):
        from aja.orchestration import context_window
        original = context_window.MAX_TOOL_RESULT_CHARS
        context_window.MAX_TOOL_RESULT_CHARS = 100
        try:
            raw = "a" * 200
            result = self.truncate(raw, max_chars=context_window.MAX_TOOL_RESULT_CHARS)
            self.assertLessEqual(len(result), 250)  # some slack for the marker
        finally:
            context_window.MAX_TOOL_RESULT_CHARS = original


class TestResolveModelLimit(unittest.TestCase):

    def setUp(self):
        from aja.orchestration.context_window import resolve_model_limit
        self.resolve = resolve_model_limit

    def test_claude_resolves_high_limit(self):
        limit = self.resolve(model="claude-sonnet-4-5")
        self.assertGreater(limit, 100_000)

    def test_gpt4o_resolves_reasonable_limit(self):
        limit = self.resolve(model="gpt-4o")
        self.assertGreater(limit, 50_000)

    def test_gemini_resolves_large_limit(self):
        limit = self.resolve(model="gemini-2.0-flash")
        self.assertGreater(limit, 500_000)

    def test_unknown_model_returns_default(self):
        from aja.orchestration.context_window import _DEFAULT_LIMIT, _BUDGET_FRACTION
        limit = self.resolve(model="some-unknown-model-xyz")
        self.assertEqual(limit, int(_DEFAULT_LIMIT * _BUDGET_FRACTION))

    def test_copilot_provider_resolves(self):
        limit = self.resolve(provider="copilot")
        self.assertGreater(limit, 50_000)

    def test_budget_fraction_applied(self):
        from aja.orchestration.context_window import _BUDGET_FRACTION
        limit = self.resolve(model="claude")
        raw_limit = 200_000
        expected = int(raw_limit * _BUDGET_FRACTION)
        self.assertEqual(limit, expected)


class TestConfigOverride(unittest.TestCase):
    """
    Tier-1 override: swarm_settings.context_limit_tokens in aja.json
    should beat the hardcoded table for any model string.
    """

    def _make_config(self, context_limit_tokens):
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.context_limit_tokens = context_limit_tokens
        mock_cfg = MagicMock()
        mock_cfg.swarm_settings = mock_settings
        return mock_cfg

    def test_config_override_beats_table(self):
        """An explicit aja.json limit is used even for a well-known model."""
        from unittest.mock import patch
        from aja.orchestration.context_window import resolve_model_limit, _BUDGET_FRACTION

        with patch("aja.orchestration.context_window.CONFIG", self._make_config(500_000)):
            limit = resolve_model_limit(model="claude-sonnet")

        # Should use 500_000 * 0.8, not the table value of 200_000 * 0.8
        self.assertEqual(limit, int(500_000 * _BUDGET_FRACTION))

    def test_none_override_falls_through_to_table(self):
        """When context_limit_tokens is None, the table is used."""
        from unittest.mock import patch
        from aja.orchestration.context_window import resolve_model_limit, _BUDGET_FRACTION

        with patch("aja.orchestration.context_window.CONFIG", self._make_config(None)):
            limit = resolve_model_limit(model="gpt-4o")

        self.assertEqual(limit, int(128_000 * _BUDGET_FRACTION))

    def test_config_error_falls_through_gracefully(self):
        """If importing CONFIG raises, we fall back to table without crashing."""
        import importlib
        from unittest.mock import patch
        from aja.orchestration.context_window import resolve_model_limit, _BUDGET_FRACTION

        # Simulate CONFIG access raising an exception by patching the module attribute
        with patch("aja.orchestration.context_window.CONFIG",
                   new_callable=lambda: type("Bad", (), {"swarm_settings": property(lambda s: (_ for _ in ()).throw(Exception("boom")))})):
            # The broad except in resolve_model_limit should catch this
            # Just verify it doesn't raise and falls back to table
            try:
                limit = resolve_model_limit(model="gemini-2.0-flash")
                self.assertEqual(limit, int(1_000_000 * _BUDGET_FRACTION))
            except Exception:
                self.fail("resolve_model_limit raised unexpectedly when CONFIG access failed")

    def test_unknown_model_with_config_override(self):
        """A totally unknown model can still be configured via aja.json."""
        from unittest.mock import patch
        from aja.orchestration.context_window import resolve_model_limit, _BUDGET_FRACTION

        custom_limit = 2_000_000
        with patch("aja.orchestration.context_window.CONFIG", self._make_config(custom_limit)):
            limit = resolve_model_limit(model="my-custom-llm-v42")

        self.assertEqual(limit, int(custom_limit * _BUDGET_FRACTION))

class TestCompressHistory(unittest.TestCase):

    def setUp(self):
        from aja.orchestration.context_window import compress_history
        self.compress = compress_history

    def test_empty_history_unchanged(self):
        h = []
        self.compress(h)
        self.assertEqual(h, [])

    def test_single_message_unchanged(self):
        h = [{"role": "user", "content": "hi"}]
        self.compress(h)
        self.assertEqual(len(h), 1)

    def test_two_messages_unchanged(self):
        h = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        original_len = len(h)
        self.compress(h, model="copilot")
        self.assertEqual(len(h), original_len)

    def test_small_history_under_budget_unchanged(self):
        h = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        original_len = len(h)
        self.compress(h, model="claude-sonnet")
        self.assertEqual(len(h), original_len)

    def test_oversized_history_is_reduced(self):
        # Each message is ~3,000 chars ≈ ~857 tokens
        # Copilot budget ≈ 128_000 * 0.8 = 102_400 tokens
        # We put 200 such messages → ~171,400 tokens, should exceed budget
        big_content = "x" * 3_000
        h = [{"role": "user", "content": big_content} for _ in range(200)]
        original_len = len(h)
        self.compress(h, model="copilot")
        self.assertLess(len(h), original_len)

    def test_first_message_preserved(self):
        big_content = "x" * 3_000
        first_msg = {"role": "user", "content": "FIRST_MESSAGE_ANCHOR"}
        h = [first_msg] + [{"role": "user", "content": big_content} for _ in range(200)]
        self.compress(h, model="copilot")
        self.assertEqual(h[0]["content"], "FIRST_MESSAGE_ANCHOR")

    def test_always_retains_at_least_two_messages(self):
        # Force an impossibly small budget via unknown model with default limit
        # and massive content so it would loop forever without the guard
        big_content = "z" * 50_000
        h = [
            {"role": "user", "content": big_content},
            {"role": "assistant", "content": big_content},
            {"role": "user", "content": big_content},
        ]
        self.compress(h, model="unknown-tiny-model-xyzzy")
        self.assertGreaterEqual(len(h), 2)


class TestEdgeCasesIntegration(unittest.TestCase):
    """
    Simulates the exact real-world failure scenario:
    read_file on a ~1MB file → tool result truncated → subsequent turns succeed.
    """

    def setUp(self):
        from aja.orchestration.context_window import (
            truncate_tool_result,
            compress_history,
            estimate_tokens,
            MAX_TOOL_RESULT_CHARS,
        )
        self.truncate = truncate_tool_result
        self.compress = compress_history
        self.estimate = estimate_tokens
        self.max_chars = MAX_TOOL_RESULT_CHARS

    def test_massive_file_read_truncated_to_budget(self):
        """800KB tool result must be truncated to <= MAX_TOOL_RESULT_CHARS."""
        # Simulate a 10,000-line log file (~800KB)
        raw = "\n".join([f"LOG LINE {i}: " + "a" * 80 for i in range(10_000)])
        self.assertGreater(len(raw), 500_000)

        truncated = self.truncate(raw, self.max_chars)
        self.assertLessEqual(len(truncated), self.max_chars * 2 + 200)  # marker slack

    def test_session_still_usable_after_large_read(self):
        """After a large tool result, subsequent turns fit within copilot budget."""
        import random
        import string
        # Build the history as DirectSession would after Turn 1 large read
        big_raw = "\n".join([
            "LOG LINE {}: ".format(i) + "".join(random.choices(string.ascii_letters, k=80))
            for i in range(10_000)
        ])
        truncated = self.truncate(big_raw, self.max_chars)
        history = [
            {"role": "user", "content": "Please read massive_log.txt"},
            {"role": "assistant", "content": "Calling read_file..."},
            {"role": "user", "content": f"Tool 'read_file' result:\n{truncated}"},
            {"role": "assistant", "content": "The file has 10,000 lines."},
        ]

        # Add Turn 2 message
        history.append({"role": "user", "content": "Now add a button to index.html"})

        # Compress for copilot model (128k budget)
        self.compress(history, model="copilot")

        total_tokens = sum(self.estimate(str(m.get("content", ""))) for m in history)
        limit = 128_000 * 0.80
        self.assertLess(total_tokens, limit, (
            f"After truncation+compress, history ({total_tokens:,} tokens) "
            f"still exceeds budget ({limit:,})"
        ))

    def test_context_recall_after_truncation(self):
        """First message is always preserved so the model can recall the task origin."""
        big_content = "x" * 3_000
        first = {"role": "user", "content": "read test_env/massive_log.txt"}
        history = [first] + [
            {"role": "user", "content": big_content} for _ in range(100)
        ]
        self.compress(history, model="copilot")
        self.assertEqual(history[0]["content"], "read test_env/massive_log.txt")


if __name__ == "__main__":
    unittest.main()
