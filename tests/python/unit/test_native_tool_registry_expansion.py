import subprocess
import time
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import aja.config
from aja.orchestration.tools.native import NativeToolRegistry


def test_git_tools(tmp_path):
    # Initialize a temporary git repository
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    
    orig_root = aja.config.PROJECT_ROOT
    try:
        aja.config.PROJECT_ROOT = tmp_path
        registry = NativeToolRegistry()
        
        # Test git_status on empty repo
        res = registry.execute("git_status", {})
        assert "clean" in res or res == ""
        
        # Create a file
        file_path = tmp_path / "test.txt"
        file_path.write_text("hello", encoding="utf-8")
        
        # Status should show untracked file
        res = registry.execute("git_status", {})
        assert "test.txt" in res
        
        # Test git_diff
        res_diff = registry.execute("git_diff", {})
        assert "No changes detected" in res_diff or "No uncommitted working tree changes detected" in res_diff
        
        # Stage the file and commit
        subprocess.run(["git", "add", "test.txt"], cwd=str(tmp_path), capture_output=True)
        res_commit = registry.execute("git_commit", {"message": "initial commit"})
        assert "initial commit" in res_commit or "master" in res_commit or "main" in res_commit
        
    finally:
        aja.config.PROJECT_ROOT = orig_root


def test_http_fetch():
    registry = NativeToolRegistry()
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b"mock web page content"
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        res = registry.execute("http_fetch", {"url": "https://example.com"})
        assert res == "mock web page content"


def test_apply_patch(tmp_path):
    # Initialize git in tmp_path
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    
    file_path = tmp_path / "hello.txt"
    file_path.write_text("line 1\nline 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add hello"], cwd=str(tmp_path), capture_output=True)
    
    orig_root = aja.config.PROJECT_ROOT
    try:
        aja.config.PROJECT_ROOT = tmp_path
        registry = NativeToolRegistry()
        
        diff_text = """diff --git a/hello.txt b/hello.txt
index 1234567..890abcd 100644
--- a/hello.txt
+++ b/hello.txt
@@ -1,2 +1,2 @@
 line 1
-line 2
+line 2 patched
"""
        res = registry.execute("apply_patch", {"path": "hello.txt", "diff_text": diff_text})
        assert "Successfully applied patch" in res
        assert file_path.read_text(encoding="utf-8") == "line 1\nline 2 patched\n"
    finally:
         aja.config.PROJECT_ROOT = orig_root


def test_delete_path(tmp_path):
    orig_root = aja.config.PROJECT_ROOT
    orig_allow = getattr(aja.config.CONFIG.swarm_settings, "allow_out_of_bounds_paths", False)
    try:
        aja.config.PROJECT_ROOT = tmp_path
        aja.config.CONFIG.swarm_settings.allow_out_of_bounds_paths = False
        registry = NativeToolRegistry()
        
        # Delete file
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        res = registry.execute("delete_path", {"path": str(f)})
        assert "Successfully deleted file" in res
        assert not f.exists()
        
        # Try deleting outside root (boundary protection)
        res_security = registry.execute("delete_path", {"path": "C:\\Windows\\system32"})
        assert "Security Error" in res_security
    finally:
        aja.config.PROJECT_ROOT = orig_root
        aja.config.CONFIG.swarm_settings.allow_out_of_bounds_paths = orig_allow


def test_copy_move_path(tmp_path):
    orig_root = aja.config.PROJECT_ROOT
    try:
        aja.config.PROJECT_ROOT = tmp_path
        registry = NativeToolRegistry()
        
        src = tmp_path / "src.txt"
        src.write_text("hello", encoding="utf-8")
        dest = tmp_path / "dest.txt"
        
        # Copy
        res_copy = registry.execute("copy_path", {"src": str(src), "dest": str(dest)})
        assert "Successfully copied file" in res_copy
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "hello"
        
        # Move
        dest2 = tmp_path / "dest2.txt"
        res_move = registry.execute("move_path", {"src": str(dest), "dest": str(dest2)})
        assert "Successfully moved" in res_move
        assert not dest.exists()
        assert dest2.exists()
        assert dest2.read_text(encoding="utf-8") == "hello"
    finally:
        aja.config.PROJECT_ROOT = orig_root


def test_query_past_experiences():
    from aja.memory.experience_store import experience_store
    
    # Store backup
    backup_store = experience_store.store
    backup_enabled = experience_store.learning_enabled
    backup_service = experience_store.embedding_service
    
    try:
        experience_store.store = [
            {
                "goal": "run pytest tests",
                "goal_embedding": [0.1] * 384,
                "embedding_model": "mock-model",
                "plan_structure": "plan details",
                "success": True,
                "latency": 1.2,
                "fail_reason": "",
                "timestamp": time.time()
            }
        ]
        experience_store.learning_enabled = True
        
        class MockEmbedding:
            def embed(self, text):
                return [0.1] * 384
            def get_model_name(self):
                return "mock-model"
                
        experience_store.embedding_service = MockEmbedding()
        
        registry = NativeToolRegistry()
        res = registry.execute("query_past_experiences", {"query": "pytest"})
        assert "Goal: run pytest tests" in res
        assert "Status: SUCCESS" in res
    finally:
        experience_store.store = backup_store
        experience_store.learning_enabled = backup_enabled
        experience_store.embedding_service = backup_service


def test_shell_and_bash_tool_aliases():
    registry = NativeToolRegistry()
    assert "shell" in registry.tools
    assert "bash" in registry.tools

    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "hello alias"
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        # Test execute with name="shell" and arguments={"command": "echo hello alias"}
        res = registry.execute("shell", {"command": "echo hello alias"})
        assert res == "hello alias"

        # Test execute with name="bash" and arguments={"cmd": "echo hello alias"}
        res2 = registry.execute("bash", {"cmd": "echo hello alias"})
        assert res2 == "hello alias"

        # Test dispatch normalization
        act = registry.dispatch("shell", {"command": "echo hello alias"}, trace_id="tr-1")
        assert act.tool == "run_shell_command"
        assert act.args["cmd"] == "echo hello alias"


def test_send_telegram_message_schema():
    registry = NativeToolRegistry()
    schema_names = [s["function"]["name"] for s in registry.get_schemas()]
    assert "send_telegram_message" in schema_names


def test_send_telegram_message_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:mock_token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "445566")
    registry = NativeToolRegistry()

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        res = registry.execute("send_telegram_message", {"message": "ping from test"})
        assert "Telegram message successfully sent to chat 445566" in res

        mock_post.assert_called_once_with(
            "https://api.telegram.org/bot123:mock_token/sendMessage",
            json={"chat_id": "445566", "text": "ping from test"},
            timeout=10,
        )


def test_send_telegram_message_alias_and_param_mapping(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:mock_token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "445566")
    registry = NativeToolRegistry()

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        # Test execute via notify_telegram alias with text argument
        res = registry.execute("notify_telegram", {"text": "hello via alias"})
        assert "Telegram message successfully sent to chat 445566" in res
        assert mock_post.call_args[1]["json"]["text"] == "hello via alias"

        # Test dispatch normalization
        act = registry.dispatch("notify_telegram", {"text": "hello via alias"}, trace_id="tr-tg")
        assert act.tool == "send_telegram_message"
        assert act.args["message"] == "hello via alias"


def test_send_telegram_message_missing_config(monkeypatch):
    registry = NativeToolRegistry()

    # Missing token
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "445566")
    res_no_tok = registry.execute("send_telegram_message", {"message": "ping"})
    assert "bot token not configured" in res_no_tok.lower()

    # Missing chat id
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:mock_token")
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    res_no_chat = registry.execute("send_telegram_message", {"message": "ping"})
    assert "chat/user id not configured" in res_no_chat.lower()

    # Empty message
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "445566")
    res_empty = registry.execute("send_telegram_message", {"message": ""})
    assert "'message' parameter is required" in res_empty


def test_send_telegram_message_redacts_token_on_error(monkeypatch):
    secret_token = "123456:secret_bot_token_abc"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret_token)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "445566")
    registry = NativeToolRegistry()

    with patch("requests.post", side_effect=RuntimeError(f"Failed to reach https://api.telegram.org/bot{secret_token}/sendMessage")):
        res = registry.execute("send_telegram_message", {"message": "ping"})
        assert secret_token not in res
        assert "[REDACTED_BOT_TOKEN]" in res

