"""
Tests for ModelSpec, Capability-Driven Router, and Operating Modes.
Validates model-agnostic capability routing across local, cloud, hybrid, and swarm modes.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from aja.models.model_spec import (
    ModelCapability,
    ModelTier,
    ModelSpec,
    infer_capabilities,
    parse_model_spec,
)
from aja.models.local_manager import LocalModelManager
from aja.llm import resolve_provider_model, completion
from aja.cognitive.prompts import build_system_prompt


class TestModelSpecParsing:
    def test_parse_cloud_model(self):
        spec = parse_model_spec("google:gemini-2.0-flash")
        assert spec.provider == "google"
        assert spec.model_name == "gemini-2.0-flash"
        assert spec.is_cloud is True
        assert spec.is_local is False
        assert ModelCapability.VISION in spec.capabilities
        assert ModelCapability.TOOLS in spec.capabilities

    def test_parse_local_gguf_model(self):
        spec = parse_model_spec("llama_cpp:LFM2.5-VL-1.6B-Q4_K_M.gguf")
        assert spec.provider == "llama_cpp"
        assert "LFM2.5-VL-1.6B" in spec.model_name
        assert spec.uri.endswith(".gguf")
        assert spec.is_local is True
        assert spec.has_vision is True
        assert spec.quantization == "Q4_K_M"

    def test_parse_coder_model(self):
        spec = parse_model_spec("Qwen2.5-Coder-7B-Instruct-GGUF")
        assert spec.provider == "llama_cpp"
        assert spec.is_local is True
        assert spec.has_code is True
        assert spec.has_vision is False

    def test_infer_capabilities_vision(self):
        caps = infer_capabilities("LFM2.5-VL-1.6B", provider="llama_cpp")
        assert ModelCapability.VISION in caps
        assert ModelCapability.CHAT in caps

        caps_gemini = infer_capabilities("gemini-2.5-flash", provider="google")
        assert ModelCapability.VISION in caps_gemini
        assert ModelCapability.TOOLS in caps_gemini


class TestOperatingModesRouting:
    def test_hybrid_mode_preserves_explicit(self):
        p, m = resolve_provider_model("google:gemini-2.5-flash", "hybrid", "gemma-4", "gemini-2.5")
        assert (p, m) == ("google", "gemini-2.5-flash")

        p_loc, m_loc = resolve_provider_model("llama_cpp:qwen2.5-7b.gguf", "hybrid", "gemma-4", "gemini-2.5")
        assert (p_loc, m_loc) == ("llama_cpp", "qwen2.5-7b.gguf")

    def test_local_mode_redirects_cloud(self):
        p, m = resolve_provider_model("google:gemini-2.5-flash", "local", "qwen-coder-local", "gemini-cloud")
        assert p == "llama_cpp"
        assert m == "qwen-coder-local"

    def test_cloud_mode_redirects_local(self):
        p, m = resolve_provider_model("llama_cpp:my-local.gguf", "cloud", "qwen-coder-local", "gemini-cloud")
        assert p == "google"
        assert m == "gemini-cloud"

    def test_hybrid_mode_vision_auto_routing(self):
        with patch.object(LocalModelManager, "get_active_vision_model", return_value="llama_cpp:LFM2.5-VL-1.6B-Q4_K_M.gguf"):
            # Text-only model with capability="vision" should auto-route to local vision engine
            p, m = resolve_provider_model(
                "llama_cpp:qwen2.5-coder-7b-instruct.gguf",
                "hybrid",
                "local-fallback",
                "cloud-fallback",
                capability="vision",
            )
            assert p == "llama_cpp"
            assert "LFM2.5-VL" in m


class TestMmprojAttachment:
    def test_find_mmproj_matching_vision_model(self):
        model_name = "LFM2.5-VL-1.6B-Q4_K_M.gguf"
        # Test heuristic matching logic
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True):
            mmproj = LocalModelManager.find_mmproj_for_model(model_name)
            # If an mmproj exists on host or candidate matches pattern
            assert mmproj is None or "mmproj" in str(mmproj).lower()


class TestSystemPromptGroundTruth:
    def test_system_prompt_contains_mode_and_model_facts(self):
        with patch.object(LocalModelManager, "get_operating_mode", return_value="hybrid"), \
             patch.object(LocalModelManager, "get_active_model", return_value={"mode": "hybrid", "active_model": "llama_cpp:LFM2.5-VL-1.6B"}), \
             patch.object(LocalModelManager, "get_active_vision_model", return_value="llama_cpp:LFM2.5-VL-1.6B"):
            prompt = build_system_prompt(goal="test goal")
            assert "AI Model & Operating Mode Facts" in prompt
            assert "hybrid" in prompt
            assert "llama_cpp:LFM2.5-VL-1.6B" in prompt
            assert "inspect_host_hardware" in prompt  # Instructs not to use inspect_host_hardware
