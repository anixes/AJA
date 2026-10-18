import pytest
from unittest.mock import patch
from aja.interface.intent_parser import parse_intent, local_router_fallback

def test_local_router_control_commands():
    # tui and aliases
    res = local_router_fallback("aja tui")
    assert res is not None
    assert res["type"] == "control"
    assert res["command"] == "tui"

    res = local_router_fallback("open tui")
    assert res is not None
    assert res["command"] == "tui"

    res = local_router_fallback("dashboard")
    assert res is not None
    assert res["command"] == "tui"

    # kanban and aliases
    res = local_router_fallback("aja kanban")
    assert res is not None
    assert res["type"] == "control"
    assert res["command"] == "kanban"

    res = local_router_fallback("/live")
    assert res is not None
    assert res["command"] == "kanban"

    # help and models
    assert local_router_fallback("help")["command"] == "help"
    assert local_router_fallback("models")["command"] == "models"
    assert local_router_fallback("clear")["command"] == "clear"

    # doctor
    res = local_router_fallback("doctor")
    assert res is not None
    assert res["type"] == "control"
    assert res["command"] == "doctor"

    res = local_router_fallback("run system doctor")
    assert res is not None
    assert res["command"] == "doctor"

    # status
    res = local_router_fallback("swarm status")
    assert res is not None
    assert res["command"] == "status"

    # gpu
    res = local_router_fallback("gpu status")
    assert res is not None
    assert res["command"] == "gpu"

    # logs
    res = local_router_fallback("show logs")
    assert res is not None
    assert res["command"] == "logs"

    # exit
    res = local_router_fallback("exit")
    assert res is not None
    assert res["command"] == "exit"

    # pause/resume
    assert local_router_fallback("pause")["command"] == "pause"
    assert local_router_fallback("resume")["command"] == "resume"

def test_local_router_git_status():
    res = local_router_fallback("git status")
    assert res is not None
    assert res["type"] == "tool_calls"
    assert res["tool_calls"] == [{"tool": "git_status", "args": {}}]

def test_local_router_ls_dir():
    # default path
    res = local_router_fallback("ls")
    assert res is not None
    assert res["tool_calls"][0]["tool"] == "list_directory"
    assert res["tool_calls"][0]["args"]["path"] == "."

    # specific path
    res = local_router_fallback("list files in D:/foo")
    assert res is not None
    assert res["tool_calls"][0]["args"]["path"] == "D:/foo"

    # phrase mapping
    res = local_router_fallback("list all files in the current workspace directory")
    assert res is not None
    assert res["tool_calls"][0]["args"]["path"] == "."

def test_local_router_conversational_ls_fallback():
    # Natural language queries should bypass regex and fall back to LLM
    assert local_router_fallback("list files in data science folder inside d drive") is None
    assert local_router_fallback("hey can you list the files in the data science folder please?") is None
    assert local_router_fallback("show me the files in that data science directory") is None
    assert local_router_fallback("what files are in the data science folder?") is None

def test_local_router_cat_read():
    res = local_router_fallback("cat README.md")
    assert res is not None
    assert res["tool_calls"][0]["tool"] == "read_file"
    assert res["tool_calls"][0]["args"]["path"] == "README.md"

    res = local_router_fallback("view file \"D:/data science/README.md\"")
    assert res is not None
    assert res["tool_calls"][0]["args"]["path"] == "D:/data science/README.md"

def test_local_router_conversational_cat_fallback():
    # Natural language queries should bypass regex and fall back to LLM
    assert local_router_fallback("read that config file for me") is None
    assert local_router_fallback("can you read the configuration file please") is None
    assert local_router_fallback("show contents of that random file") is None

def test_local_router_grep():
    res = local_router_fallback("search for 'Arrow' in libs/handover.py")
    assert res is not None
    assert res["tool_calls"][0]["tool"] == "grep_search"
    assert res["tool_calls"][0]["args"]["query"] == "Arrow"
    assert res["tool_calls"][0]["args"]["path"] == "libs/handover.py"

def test_local_router_greetings_and_pleasantries():
    # Standalone greetings should match fast path instantly
    res = local_router_fallback("hello")
    assert res is not None
    assert res["type"] == "question"
    assert "Hello" in res["response"]

    res = local_router_fallback("hi!")
    assert res is not None
    assert res["type"] == "question"

    res = local_router_fallback("good morning")
    assert res is not None
    assert res["type"] == "question"

    res = local_router_fallback("thank you")
    assert res is not None
    assert res["type"] == "question"
    assert "welcome" in res["response"].lower()

    res = local_router_fallback("help")
    assert res is not None
    assert res["type"] == "control"
    assert res["command"] == "help"


def test_local_router_fallback_no_match():
    # Compound instructions and open-ended questions must bypass regex and fall back to LLM
    assert local_router_fallback("hello, please refactor this file") is None
    assert local_router_fallback("hi can you check the git diff for me") is None
    assert local_router_fallback("how do I configure AJA?") is None
    assert local_router_fallback("explain the system architecture") is None


@patch("aja.llm.completion")
def test_parse_intent_with_llm_fallback(mock_completion):
    mock_completion.return_value = '{"type": "question", "response": "Architecture details...", "confidence": 1.0, "goal": null, "command": null, "tool_calls": null}'

    # Non-matched command should fallback to completion
    res = parse_intent("explain the system architecture", [])
    assert res["type"] == "question"
    assert res["response"] == "Architecture details..."
    mock_completion.assert_called_once()
