"""
tests/python/unit/test_local_model_manager.py
=============================================
Unit tests for LocalModelManager and local model CLI commands.
Tests:
1. Discovery of Ollama models from mock JSON API.
2. Discovery of llama.cpp / LM Studio models from mock /v1/models API.
3. Activation of local model updating runtime config and setting operating_mode='hybrid'.
4. Offline probe resilience when local engines are stopped.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from aja.models.local_manager import (
    EngineStatus,
    LocalModelInfo,
    LocalModelManager,
)


def test_local_model_discovery_mock_ollama():
    """Verify parsing of Ollama's /api/tags payload into LocalModelInfo instances."""
    mock_payload = {
        "models": [
            {
                "name": "qwen2.5-coder:7b",
                "size": 4700000000,
                "details": {
                    "parameter_size": "7B",
                    "quantization_level": "Q4_K_M",
                },
                "modified_at": "2026-09-01T10:00:00Z",
            },
            {
                "name": "deepseek-r1:8b",
                "size": 5200000000,
                "details": {
                    "parameter_size": "8B",
                    "quantization_level": "Q4_0",
                },
                "modified_at": "2026-09-02T12:00:00Z",
            },
        ]
    }

    def fake_fetch(url, timeout=1.0):
        if "11434" in url:
            return mock_payload
        return None

    with patch.object(LocalModelManager, "_fetch_json", side_effect=fake_fetch):
        models = LocalModelManager.discover_models()

        assert len(models) == 2
        qwen = next(m for m in models if "qwen" in m.name)
        assert qwen.engine == "ollama"
        assert qwen.uri == "ollama:qwen2.5-coder:7b"
        assert qwen.parameter_size == "7B"
        assert qwen.quantization == "Q4_K_M"
        assert qwen.size_gb is not None and qwen.size_gb > 4.0


def test_local_model_discovery_mock_llama_cpp():
    """Verify parsing of llama.cpp OpenAI-compatible /v1/models endpoint."""
    mock_payload = {
        "data": [
            {"id": "meta-llama-3-8b-instruct.Q4_K_M.gguf"},
        ]
    }

    def fake_fetch(url, timeout=1.0):
        if "8080" in url:
            return mock_payload
        return None

    with patch.object(LocalModelManager, "_fetch_json", side_effect=fake_fetch):
        models = LocalModelManager.discover_models()

        assert len(models) == 1
        model = models[0]
        assert model.engine == "llama_cpp"
        assert "meta-llama" in model.name
        assert model.uri.startswith("llama_cpp:")


def test_engine_probing_offline_resilience():
    """Verify that probe_engines gracefully handles all endpoints being offline."""
    with patch.object(LocalModelManager, "_fetch_json", return_value=None):
        statuses = LocalModelManager.probe_engines()

        assert "ollama" in statuses
        assert "llama_cpp" in statuses
        assert "lm_studio" in statuses

        for name, st in statuses.items():
            assert isinstance(st, EngineStatus)
            assert st.running is False
            assert st.models_count == 0


def test_local_model_activation_persists(tmp_path):
    """Verify that activating a local model sets operating_mode='hybrid' and updates runtime config."""
    test_json = tmp_path / "aja.json"

    with patch("aja.models.local_manager.DATA_DIR", tmp_path), patch("aja.config.DATA_DIR", tmp_path):
        success = LocalModelManager.activate_model("ollama:qwen2.5-coder:7b", role="worker")
        assert success is True

        # Check persisted json
        assert test_json.exists()
        saved = json.loads(test_json.read_text(encoding="utf-8"))
        assert saved["swarm_settings"]["models"]["worker"] == "ollama:qwen2.5-coder:7b"
        assert saved["swarm_settings"]["operating_mode"] == "hybrid"

        # Check in-memory config update
        import aja.config
        assert aja.config.AJA_WORKER_MODEL == "ollama:qwen2.5-coder:7b"
