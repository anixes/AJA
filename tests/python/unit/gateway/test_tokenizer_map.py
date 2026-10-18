"""Tokenizer map tests: per-family estimation strategies."""

import sys

import pytest

from aja.utils.tokenizer_map import (
    estimate_messages_tokens,
    estimate_tokens,
    tokenizer_family,
)

sys.path.insert(0, ".")


class TestFamilyResolution:
    def test_local_providers_map_llama(self):
        for prov in ("llama_cpp", "ollama"):
            assert tokenizer_family(prov, "qwen2.5-7b-instruct") == "llama"
            assert tokenizer_family(prov, "unknown-model") == "llama"

    def test_gemma_hint(self):
        assert tokenizer_family("ollama", "gemma2-9b") == "gemma"

    def test_cloud_families(self):
        assert tokenizer_family("google", "gemini-2.0-flash") == "gemini"
        assert tokenizer_family("openai", "gpt-4o") == "cl100k"
        assert tokenizer_family("copilot", "gpt-4o-mini") == "cl100k"
        assert tokenizer_family("anthropic", "claude-3-5-sonnet") == "cl100k"


class TestEstimation:
    def test_cl100k_uses_native_when_available(self):
        """When aja_native is importable, cl100k is exact (not heuristic)."""
        try:
            from aja import aja_native  # noqa: F401

            exact = aja_native.count_tokens("hello world, this is a token count test")
        except Exception:
            pytest.skip("aja_native unavailable")
        assert estimate_tokens("hello world, this is a token count test", "openai", "gpt-4o") == exact

    def test_heuristic_within_reasonable_band(self):
        text = "The quick brown fox jumps over the lazy dog. " * 50  # 2300 chars
        est = estimate_tokens(text, "ollama", "qwen2.5-7b")
        # llama family ~3.5 ch/tok -> expect roughly 2300/3.5 = 657 ±20%
        assert 500 < est < 800

    def test_empty_text(self):
        assert estimate_tokens("", "openai", "gpt-4o") == 0

    def test_messages_overhead_applied(self):
        msgs = [{"role": "user", "content": "x" * 350}] * 3
        total = estimate_messages_tokens(msgs, "ollama", "qwen2.5-7b")
        single = estimate_tokens("x" * 350, "ollama", "qwen2.5-7b")
        assert total >= single * 3  # overhead only adds

    def test_multimodal_blocks_stringified(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "x" * 700}]}]
        total = estimate_messages_tokens(msgs, "ollama", "qwen2.5-7b")
        assert total > 100
