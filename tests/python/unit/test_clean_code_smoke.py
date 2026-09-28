import json
from aja.config import load_and_validate_config
from aja.orchestration.context_window import _pop_leading_tool_messages, atomic_prune_messages
from aja.orchestration.direct_loop import _extract_bash_commands
from aja.orchestration.tools.native import _convert_to_jupyter_notebook


def test_basic_math():
    assert 2 + 2 == 4


def test_multiplication():
    assert 3 * 3 == 9


def test_division():
    assert 10 / 2 == 5


def test_extract_bash_and_sh_commands():
    content = (
        "Run these commands:\n"
        "```bash\n"
        "git status\n"
        "```\n"
        "And then:\n"
        "```sh\n"
        "pytest -v\n"
        "```\n"
    )
    cmds = _extract_bash_commands(content)
    assert cmds == ["git status", "pytest -v"]


def test_pop_leading_tool_messages():
    messages = [
        {"role": "user", "content": "objective"},
        {"role": "tool", "content": "tool 1"},
        {"role": "tool", "content": "tool 2"},
        {"role": "assistant", "content": "response"},
    ]
    dropped = _pop_leading_tool_messages(messages)
    assert dropped == 2
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_convert_to_jupyter_notebook_basic():
    py_code = "import math\nprint(math.pi)"
    nb_json = _convert_to_jupyter_notebook(py_code)
    nb = json.loads(nb_json)
    assert nb["nbformat"] == 4
    assert len(nb["cells"]) == 1
    assert nb["cells"][0]["cell_type"] == "code"


def test_load_and_validate_config():
    cfg = load_and_validate_config()
    assert cfg is not None
    assert hasattr(cfg, "swarm_settings")
