import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aja.models.local_manager import (
    HostHardwareProfile,
    HostHardwareProfiler,
    LocalModelInfo,
    LocalModelManager,
)
from aja.gateway.telegram_local import (
    build_local_models_card,
    format_short_model_name,
    handle_local_model_callback,
)
from aja.orchestration.tools.native import NativeToolRegistry


def test_format_short_model_name():
    assert "Qwen 2.5 Coder 7B" in format_short_model_name("qwen2.5-coder-7b-instruct-q3_k_m.gguf") or "Qwen2.5 Coder 7B" in format_short_model_name("qwen2.5-coder-7b-instruct-q3_k_m.gguf")
    assert "Gemma 4 E2B" in format_short_model_name("gemma-4-E2B-it-Q4_K_M.gguf")
    assert "Vl" in format_short_model_name("Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf")


def test_host_hardware_profiler():
    profile = HostHardwareProfiler.get_profile()
    assert isinstance(profile, HostHardwareProfile)
    assert profile.os_name in ("Windows", "Linux", "Darwin")
    assert profile.cpu_cores >= 1
    assert profile.ram_total_gb > 0.0
    data = profile.to_dict()
    assert "os_name" in data
    assert "ram_total_gb" in data
    assert "has_cuda" in data


def test_scan_disk_gguf_models():
    models = LocalModelManager.scan_disk_gguf_models()
    assert isinstance(models, list)
    if models:
        m = models[0]
        assert isinstance(m, LocalModelInfo)
        assert m.name.endswith(".gguf")
        assert m.auto_tuned_ngl is not None
        assert m.auto_tuned_ngl in (99, 28, 16)
        assert m.recommendation is not None


def test_build_local_models_card():
    text, markup = build_local_models_card()
    assert "AJA Host Hardware & Local Models" in text
    assert "Host System" in text
    assert "Active Agent Roles" in text

    if markup:
        for row in markup.inline_keyboard:
            for btn in row:
                # Strictly assert Telegram 64-byte callback_data constraint
                assert len(btn.callback_data.encode("utf-8")) <= 64
                assert len(btn.text) > 0


def test_handle_local_model_callback_unauthorized(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123456789")
    auth, msg, markup = asyncio.run(
        handle_local_model_callback(
            data="lstp",
            callback_user_id="999999999",
            chat_id="12345",
        )
    )
    assert auth is False
    assert "Unauthorized" in msg


def test_handle_local_model_callback_stop(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123456789")
    with patch.object(LocalModelManager, "stop_llama_server", return_value=(True, "Stopped")):
        auth, msg, markup = asyncio.run(
            handle_local_model_callback(
                data="lstp",
                callback_user_id="123456789",
                chat_id="12345",
            )
        )
        assert auth is True
        assert "llama-server Stopped" in msg


def test_handle_local_model_callback_start(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123456789")
    dummy_model = LocalModelInfo(
        name="test-model.gguf",
        engine="llama_cpp",
        uri="llama_cpp:test-model.gguf",
        size_gb=2.5,
        auto_tuned_ngl=99,
        recommendation="Fastest",
    )
    with patch.object(LocalModelManager, "scan_disk_gguf_models", return_value=[dummy_model]), \
         patch.object(LocalModelManager, "start_llama_server", return_value=(True, "Started")), \
         patch.object(LocalModelManager, "activate_model", return_value=True):
        auth, msg, markup = asyncio.run(
            handle_local_model_callback(
                data="ls:0",
                callback_user_id="123456789",
                chat_id="12345",
            )
        )
        assert auth is True
        assert "Local Engine Started & Activated!" in msg
        assert "test-model.gguf" in msg


def test_native_tools_hardware_and_models():
    reg = NativeToolRegistry()
    hw_res = reg.execute("inspect_host_hardware", {})
    assert "os_name" in hw_res
    assert "ram_total_gb" in hw_res

    status_res = reg.execute("manage_local_models", {"action": "status"})
    assert "hardware" in status_res
    assert "engines" in status_res

    list_res = reg.execute("manage_local_models", {"action": "list"})
    assert isinstance(list_res, str)


def test_gateway_local_command_routing():
    from aja.gateway.orchestrator import UnifiedGateway
    from aja.gateway.base import MessageEvent, MessageType

    gw = UnifiedGateway.__new__(UnifiedGateway)
    gw.gateway_state = MagicMock()
    gw.gateway_state.get_session.return_value = {"history": []}
    gw._responder = MagicMock()
    responder = MagicMock()
    responder.send_message = AsyncMock()
    gw._responder.return_value = responder

    ev = MessageEvent(
        platform="telegram",
        chat_id="12345",
        user_id="999",
        message_type=MessageType.TEXT,
        text="/local",
        media_urls=[],
        message_id="1",
        raw_event=None,
    )

    asyncio.run(gw._process_gateway_event(ev, "12345", "corr-1", None))

    assert responder.send_message.called
    args, kwargs = responder.send_message.call_args
    assert args[0] == "12345"
    assert "Host Hardware & Local Models" in args[1]
    assert kwargs.get("reply_markup") is not None

