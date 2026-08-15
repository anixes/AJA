"""
Unit Tests: AJA CodeAct Unified Action Engine & Native Tooling
Validates CodeAct Python/Shell execution, web search/fetch, and sysadmin tools.
"""

import pytest
import os
from pathlib import Path

from aja.cognitive.codeact import CodeActExecutor
from aja.orchestration.tools.web_tools import search_web, fetch_url
from aja.orchestration.tools.sys_tools import (
    get_system_specs,
    get_disk_usage,
    get_active_ports,
)


def test_codeact_extract_code():
    executor = CodeActExecutor()

    # 1. Extract Python block
    raw_py = "Here is the code:\n```python\nx = 10 * 5\nprint(f'Result: {x}')\n```\nDone."
    lang, code = executor.extract_code(raw_py)
    assert lang == "python"
    assert "x = 10 * 5" in code
    assert "print(f'Result: {x}')" in code

    # 2. Extract Bash block
    raw_sh = "```bash\necho 'hello world'\n```"
    lang, code = executor.extract_code(raw_sh)
    assert lang == "shell"
    assert "echo 'hello world'" in code


def test_codeact_execute_python():
    executor = CodeActExecutor()
    code = "import sys; print('AJA_CODEACT_SUCCESS'); sys.exit(0)"
    res = executor.execute(code, language="python")

    assert res.status == "success"
    assert res.exit_code == 0
    assert "AJA_CODEACT_SUCCESS" in res.stdout


def test_codeact_execute_shell():
    executor = CodeActExecutor()
    code = "echo AJA_SHELL_TEST"
    res = executor.execute(code, language="shell")

    assert res.status == "success"
    assert res.exit_code == 0
    assert "AJA_SHELL_TEST" in res.stdout


def test_codeact_timeout_safeguard():
    executor = CodeActExecutor(default_timeout_seconds=1.0)
    code = "import time; time.sleep(5)"
    res = executor.execute(code, language="python", timeout=1.0)

    assert res.status == "timeout"
    assert res.exit_code == 124
    assert "timed out" in res.stderr.lower()


def test_native_web_tools():
    # 1. Test search_web
    results = search_web("Python PEP 8 style guide", limit=2)
    assert isinstance(results, list)
    assert len(results) >= 1
    assert "url" in results[0]

    # 2. Test fetch_url with basic endpoint
    fetch_res = fetch_url("https://example.com")
    assert "status" in fetch_res
    if fetch_res.get("status") == 200:
        assert "Example Domain" in fetch_res["content"]


def test_native_sys_tools():
    # 1. System specs
    specs = get_system_specs()
    assert "os" in specs
    assert "cpu_count" in specs
    assert specs["cpu_count"] >= 1

    # 2. Disk usage
    disk = get_disk_usage()
    assert "total_gb" in disk
    assert "free_gb" in disk

    # 3. Active sockets
    ports = get_active_ports()
    assert isinstance(ports, list)
