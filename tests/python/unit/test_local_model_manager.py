"""
tests/python/unit/test_local_model_manager.py
=============================================
Unit tests for LocalModelManager and local model CLI commands.
Tests:
1. Discovery of Ollama models from mock JSON API.
2. Discovery of llama.cpp / LM Studio models from mock /v1/models API.
3. Activation of local model updating runtime config and setting operating_mode='hybrid'.
4. Offline probe resilience when local engines are stopped.
5. Native scanning of local GGUF model directories and parameter/quantization extraction.
6. CUDA llama-server launch command formatting with GPU offload and Jinja chat templates.
"""

from pathlib import Path
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
        models = LocalModelManager.discover_models(include_disk=False)

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
        models = LocalModelManager.discover_models(include_disk=False)

        assert len(models) == 1
        model = models[0]
        assert "llama_cpp" in model.engine
        assert "meta-llama" in model.name
        assert model.uri.startswith("llama_cpp:")


def test_engine_probing_offline_resilience():
    """Verify that probe_engines gracefully handles all endpoints being offline."""
    with patch.object(LocalModelManager, "_fetch_json", return_value=None), patch.object(
        LocalModelManager, "scan_disk_gguf_models", return_value=[]
    ):
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


def test_scan_disk_gguf_models(tmp_path):
    """Verify disk scanner detects .gguf files, extracts metadata, and ignores mmproj files."""
    # Create fake GGUF files
    qwen = tmp_path / "qwen2.5-coder-7b-instruct-q3_k_m.gguf"
    qwen.write_bytes(b"\x00" * 1024 * 1024)  # 1 MB

    gemma = tmp_path / "gemma-4-E2B-it-Q4_K_M.gguf"
    gemma.write_bytes(b"\x00" * 2 * 1024 * 1024)  # 2 MB

    mmproj = tmp_path / "mmproj-gemma4-e2b-f16.gguf"
    mmproj.write_bytes(b"\x00" * 512)

    scanned = LocalModelManager.scan_disk_gguf_models(directory=tmp_path)
    assert len(scanned) == 2
    names = [m.name for m in scanned]
    assert "qwen2.5-coder-7b-instruct-q3_k_m.gguf" in names
    assert "gemma-4-E2B-it-Q4_K_M.gguf" in names
    assert "mmproj-gemma4-e2b-f16.gguf" not in names

    q_info = next(m for m in scanned if "qwen" in m.name)
    assert q_info.parameter_size == "7B"
    assert q_info.quantization == "Q3_K_M"

    g_info = next(m for m in scanned if "gemma" in m.name)
    assert g_info.parameter_size == "E2B"
    assert g_info.quantization == "Q4_K_M"


def test_start_llama_server_command_args(tmp_path):
    """Verify llama-server startup builds proper CUDA offload and jinja template flags."""
    fake_bin = tmp_path / "llama-server.exe"
    fake_bin.write_text("binary", encoding="utf-8")

    fake_model = tmp_path / "qwen2.5-coder-7b-instruct-q3_k_m.gguf"
    fake_model.write_bytes(b"\x00" * 1024)

    captured_cmd = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        mock_proc = MagicMock()
        return mock_proc

    with patch.object(LocalModelManager, "find_llama_server_binary", return_value=fake_bin), patch(
        "subprocess.Popen", side_effect=fake_popen
    ), patch.object(LocalModelManager, "_fetch_json", return_value={"data": [{"id": fake_model.name}]}):
        success, msg = LocalModelManager.start_llama_server(fake_model, port=8080, ngl=28, ctx=8192)

        assert success is True
        assert str(fake_bin) in captured_cmd
        assert "-m" in captured_cmd
        assert str(fake_model.resolve()) in captured_cmd
        assert "--port" in captured_cmd
        assert "8080" in captured_cmd
        assert "-ngl" in captured_cmd
        assert "28" in captured_cmd
        assert "-c" in captured_cmd
        assert "8192" in captured_cmd
        assert "--jinja" in captured_cmd
