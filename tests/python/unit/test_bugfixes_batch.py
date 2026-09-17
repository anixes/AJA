import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from aja.models.local_manager import LocalModelManager
from aja.core.conversation import ConversationCore, Final, Error, IntentResult
from aja.acp.server import ACPServer


def test_find_mmproj_with_string_path(tmp_path):
    """Verify find_mmproj_for_model does not crash with AttributeError on string paths."""
    model_file = tmp_path / "test-model-vl.gguf"
    model_file.write_bytes(b"GGUF")

    # Should safely return None (or a matching mmproj if present) without AttributeError
    result = LocalModelManager.find_mmproj_for_model(str(model_file))
    assert result is None

    # Now create an mmproj file in the same directory
    mmproj_file = tmp_path / "test-mmproj-f16.gguf"
    mmproj_file.write_bytes(b"MMPROJ")

    result2 = LocalModelManager.find_mmproj_for_model(str(model_file))
    assert result2 is not None
    assert result2.name == "test-mmproj-f16.gguf"


def test_telegram_operating_mode_callback_routing():
    """Verify callbacks starting with 'm:' are routed to handle_local_model_callback."""
    async def _run():
        from aja.gateway.tg_client import TelegramAdapter

        adapter = TelegramAdapter("MOCK_TOKEN:12345")

        mock_query = AsyncMock()
        mock_query.data = "m:loc"
        mock_query.from_user.id = 999
        mock_query.message.chat_id = 999
        mock_query.edit_message_text = AsyncMock()

        mock_update = MagicMock()
        mock_update.callback_query = mock_query

        with patch("aja.gateway.telegram_local.handle_local_model_callback", new_callable=AsyncMock) as mock_local_cb:
            mock_local_cb.return_value = (True, "Switched to local", None)
            await adapter._handle_callback(mock_update, MagicMock())

            assert mock_local_cb.called
            assert mock_local_cb.call_args[0][0] == "m:loc"
            assert adapter.metrics["callback_handled"] == 1

    asyncio.run(_run())


def test_conversation_core_mission_error_marks_failed():
    """Verify _exec_mission updates task to FAILED when an Error event is yielded."""
    async def _run():
        fake_gw = MagicMock()
        fake_reg = MagicMock()
        fake_reg.get_schemas = MagicMock(return_value=[])
        fake_exec = MagicMock()
        fake_sess = MagicMock()
        fake_sess.get = AsyncMock(return_value={})
        fake_sess.save = AsyncMock()

        core = ConversationCore(
            gateway=fake_gw,
            tools_registry=fake_reg,
            executor=fake_exec,
            sessions=fake_sess,
        )

        intent = IntentResult(
            type="MISSION",
            task="Fail this mission intentionally",
            mission_id="M-testfail1",
        )
        session = {"history": [], "tasks": [], "_working_history": []}

        async def mock_exec_chat(*args, **kwargs):
            yield Error(code="EXECUTE_FAILED", message="Test execution failure")

        with patch.object(core, "_exec_chat", side_effect=mock_exec_chat):
            with patch("aja.persistence.tasks.update_task_status") as mock_update_status:
                events = []
                async for ev in core._exec_mission(intent, session, []):
                    events.append(ev)

                assert any(isinstance(e, Error) for e in events)
                assert session["tasks"][-1]["status"] == "failed"
                # Verify update_task_status called with FAILED
                failed_calls = [call for call in mock_update_status.call_args_list if call[0][1] == "FAILED"]
                assert len(failed_calls) > 0

    asyncio.run(_run())


def test_acp_session_cancel_active_task():
    """Verify session/cancel actively cancels running prompt tasks."""
    async def _run():
        server = ACPServer(
            gateway=MagicMock(),
            tools_registry=MagicMock(),
            executor=MagicMock(),
        )
        init_res = server._handle_initialize({})
        assert init_res["agentInfo"]["name"] == "AJA"

        session_res = server._handle_session_new({})
        sid = session_res["sessionId"]

        async def slow_loop(*args, **kwargs):
            await asyncio.sleep(5)
            return {"status": "completed"}

        with patch("aja.orchestration.direct_loop.run_direct_loop", side_effect=slow_loop):
            # Start prompt task in background
            prompt_task = asyncio.create_task(server._handle_session_prompt({"sessionId": sid, "prompt": "test"}))
            # Yield control so loop starts and registers sid in active_tasks
            for _ in range(10):
                await asyncio.sleep(0.01)
                if sid in server.active_tasks:
                    break

            # Confirm task registered
            assert sid in server.active_tasks

            # Trigger cancel
            cancel_res = server._handle_session_cancel({"sessionId": sid})
            assert cancel_res["status"] == "cancelled"

            # Await prompt_task and ensure CancelledError handled
            try:
                await prompt_task
            except asyncio.CancelledError:
                pass

            # Active tasks map should be cleaned up
            assert sid not in server.active_tasks

    asyncio.run(_run())


def test_audio_transcriber_gemini_header():
    """Verify _transcribe_with_gemini sends API key in headers, not in URL query."""
    async def _run():
        from aja.gateway.audio_transcriber import _transcribe_with_gemini

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={"candidates": [{"content": {"parts": [{"text": "Hello world"}]}}]})
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)
            mock_post.return_value = mock_resp

            transcript = await _transcribe_with_gemini(b"fake_audio", "audio/ogg", "test_gemini_key_123")
            assert transcript == "Hello world"

            assert mock_post.called
            call_url = mock_post.call_args[0][0]
            call_headers = mock_post.call_args[1].get("headers", {})

            # Key must NOT be in the URL query string
            assert "key=" not in call_url
            assert "test_gemini_key_123" not in call_url
            # Key MUST be in x-goog-api-key header
            assert call_headers.get("x-goog-api-key") == "test_gemini_key_123"

    asyncio.run(_run())


def test_fetch_recent_tasks():
    """Verify fetch_recent_tasks retrieves tasks from LanceDB."""
    from aja.persistence.tasks import fetch_recent_tasks, create_task
    tid = create_task({"task": "Unit test recent tasks", "test": True})
    assert tid is not None

    recent = fetch_recent_tasks(limit=5)
    assert isinstance(recent, list)
    assert any(t.get("task_id") == tid or t.get("id") == tid for t in recent)


def test_out_of_bounds_path_validation(tmp_path):
    """Verify out-of-bounds path validation respects allow_out_of_bounds_paths and fs.read.global permissions."""
    import aja.config
    from aja.orchestration.tools.native import NativeToolRegistry

    orig_root = aja.config.PROJECT_ROOT
    orig_oob = getattr(aja.config.CONFIG.swarm_settings, "allow_out_of_bounds_paths", False)
    orig_scopes = dict(aja.config.CONFIG.permission_policy.scopes)

    # Create an artificial project root inside tmp_path, and a file outside it
    fake_project = tmp_path / "project"
    fake_project.mkdir()
    external_dir = tmp_path / "external_data"
    external_dir.mkdir()
    external_csv = external_dir / "titanic.csv"
    external_csv.write_text("PassengerId,Survived,Pclass\n1,0,3", encoding="utf-8")

    try:
        aja.config.PROJECT_ROOT = fake_project
        registry = NativeToolRegistry()

        # 1. When allow_out_of_bounds_paths is False: read is denied
        aja.config.CONFIG.swarm_settings.allow_out_of_bounds_paths = False
        err = registry._validate_path(str(external_csv), mode="read")
        assert err is not None
        assert "Security Error" in err
        assert "outside the authorized project root" in err

        # 2. When allow_out_of_bounds_paths is True: read is permitted
        aja.config.CONFIG.swarm_settings.allow_out_of_bounds_paths = True
        aja.config.CONFIG.permission_policy.scopes["fs.read.global"] = "allow"
        res_read = registry._validate_path(str(external_csv), mode="read")
        assert res_read is None
        content = registry.read_file(str(external_csv))
        assert "PassengerId,Survived,Pclass" in content

        # 3. When allow_out_of_bounds_paths is True: write is guarded by fs.write.global: ask (denied in non-interactive tests)
        res_write = registry._validate_path(str(external_csv), mode="write")
        assert res_write is not None
        assert "Security Error" in res_write

        # 4. Explicit policy deny overrides allow_out_of_bounds_paths
        aja.config.CONFIG.permission_policy.scopes["fs.read.global"] = "deny"
        err_denied = registry._validate_path(str(external_csv), mode="read")
        assert err_denied is not None
        assert "Security Error" in err_denied
    finally:
        aja.config.PROJECT_ROOT = orig_root
        aja.config.CONFIG.swarm_settings.allow_out_of_bounds_paths = orig_oob
        aja.config.CONFIG.permission_policy.scopes = orig_scopes

