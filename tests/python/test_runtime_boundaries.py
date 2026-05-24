import ast
from pathlib import Path

from aja.api.bridge import analyze_shell_command, app, start_dashboard
from aja.api.routes import ROUTE_GROUPS
from aja.security.command_guard import classify_command


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "libs" / "aja-core" / "aja"


def test_bridge_compatibility_app_exposes_route_groups():
    assert app.state.agentx_route_groups == ROUTE_GROUPS
    app_paths = {route.path for route in app.routes}

    assert "/runtime/approve" in app_paths
    assert "/memory/tasks" in app_paths
    assert "/telegram/webhook" in app_paths
    assert "/safety/history" in app_paths

    grouped_paths = {path for paths in ROUTE_GROUPS.values() for path in paths}
    assert "/runtime/approve" in grouped_paths
    assert "/memory/tasks" in grouped_paths
    assert "/telegram/webhook" in grouped_paths


def test_command_policy_service_matches_shared_guard():
    command = r"powershell -NoProfile -Command \"Remove-Item -Force -Recurse C:\tmp\old\""
    assert analyze_shell_command(command) == classify_command(command)


def test_dashboard_launcher_is_deprecated_without_spawning():
    payload = start_dashboard()

    assert payload["ok"] is False
    assert payload["status"] == "deprecated"
    assert "CLI/TUI" in payload["message"]


def test_runtime_layers_do_not_import_client_memory_directly():
    scoped_roots = [
        PACKAGE_ROOT / "runtime",
        PACKAGE_ROOT / "scheduler",
        PACKAGE_ROOT / "orchestration",
    ]
    allowed = {
        PACKAGE_ROOT / "runtime" / "lance_stores.py",
    }
    offenders = []

    for root in scoped_roots:
        for path in root.rglob("*.py"):
            if path in allowed or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "aja.memory.secretary" in text or "get_aja_memory" in text or "AJAMemory" in text:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_async_functions_do_not_call_blocking_process_network_or_sleep_directly():
    scoped_roots = [
        PACKAGE_ROOT / "api",
        PACKAGE_ROOT / "server",
        PACKAGE_ROOT / "gateway",
        PACKAGE_ROOT / "scheduler",
        PACKAGE_ROOT / "runtime",
    ]
    blocking_calls = {
        "subprocess.run",
        "subprocess.Popen",
        "urllib.request.urlopen",
        "time.sleep",
    }
    offenders = []

    for root in scoped_roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                dotted = _call_name(node.func)
                if dotted not in blocking_calls:
                    continue
                async_owner = _nearest_async_function(node, parents)
                if async_owner is None:
                    continue
                if _inside_nested_sync_function(node, async_owner, parents):
                    continue
                if _inside_asyncio_to_thread(node, async_owner, parents):
                    continue
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}:{dotted}")

    assert offenders == []


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _nearest_async_function(node, parents):
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.AsyncFunctionDef):
            return current
        current = parents.get(current)
    return None


def _inside_nested_sync_function(node, async_owner, parents):
    current = parents.get(node)
    while current is not None and current is not async_owner:
        if isinstance(current, ast.FunctionDef):
            return True
        current = parents.get(current)
    return False


def _inside_asyncio_to_thread(node, async_owner, parents):
    current = parents.get(node)
    while current is not None and current is not async_owner:
        if isinstance(current, ast.Call) and _call_name(current.func) == "asyncio.to_thread":
            return True
        current = parents.get(current)
    return False
