"""
CI Forensics Probe - Full Failure Context Collection
Run with: python scripts/forensics_probe.py
"""
import sys
import os
import asyncio
import importlib
import importlib.metadata as meta
import subprocess
import traceback

results = {}

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ─── SECTION 1: Environment ──────────────────────────────────────
section("1. PYTHON ENVIRONMENT")
print(f"Python version: {sys.version}")
print(f"Executable:     {sys.executable}")
print(f"Platform:       {sys.platform}")
print(f"os.name:        {os.name}")

# ─── SECTION 2: Core Imports ────────────────────────────────────
section("2. CORE PACKAGE IMPORTS")

for mod in ["aja", "aja.main", "aja.config", "aja.llm",
            "aja.runtime.sandbox", "aja.runtime.execution.manager",
            "aja.runtime.execution.contracts",
            "aja.runtime.execution.governance",
            "aja.runtime.handover",
            "aja.capabilities.terminal",
            "aja.orchestration.tools.executor",
            "aja.memory.manager",
            "aja.utils.diagnostics"]:
    try:
        importlib.import_module(mod)
        print(f"  OK  {mod}")
    except Exception as e:
        print(f"  FAIL {mod}: {e}")

# ─── SECTION 3: aja_native Extension ────────────────────────────
section("3. AJA_NATIVE RUST EXTENSION")
try:
    from aja import aja_native
    fns = [f for f in dir(aja_native) if not f.startswith("_")]
    print(f"  Loaded OK. Functions: {fns}")
    expected = ["write_baton", "read_baton", "write_baton_ipc", "read_baton_ipc",
                "count_tokens", "count_tokens_batch", "PyTrajectoryManager", "init_semantic"]
    for fn in expected:
        if hasattr(aja_native, fn):
            print(f"    OK  {fn}")
        else:
            print(f"    MISSING  {fn}")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

# ─── SECTION 4: Entry Points ────────────────────────────────────
section("4. ENTRY POINTS AUDIT")
try:
    eps = list(meta.entry_points(group="console_scripts"))
    aja_eps = [ep for ep in eps if "aja" in ep.name]
    print(f"  console_scripts: {[ep.name for ep in aja_eps]}")
except Exception as e:
    print(f"  console_scripts FAIL: {e}")

try:
    eps = list(meta.entry_points(group="aja.model_providers"))
    print(f"  aja.model_providers: {[ep.name for ep in eps]}")
    declared = ["google", "openai", "openrouter", "llama_cpp", "copilot"]
    missing = [p for p in declared if p not in [ep.name for ep in eps]]
    if missing:
        print(f"  MISSING FROM WHEEL: {missing}")
except Exception as e:
    print(f"  aja.model_providers FAIL: {e}")

# ─── SECTION 5: Sandbox + ExecutionManager ───────────────────────
section("5. SANDBOX EXECUTION SMOKE TEST")

async def test_execution():
    from aja.runtime.execution.manager import ExecutionManager
    from aja.runtime.execution.contracts import ExecutionRequest
    mgr = ExecutionManager()
    req = ExecutionRequest(
        command=f'"{sys.executable}" -c "print(\'exec_ok\')"',
        shell=True,
        timeout=15.0,
    )
    res = await mgr.run(req)
    return res

try:
    res = asyncio.run(test_execution())
    if res.success:
        print(f"  ExecutionManager: PASS (stdout={res.stdout.strip()!r})")
    else:
        print(f"  ExecutionManager: FAIL state={res.state} error={res.error!r}")
        print(f"    exit_code={res.exit_code}, stdout={res.stdout!r}")
except Exception as e:
    print(f"  ExecutionManager: EXCEPTION: {e}")
    traceback.print_exc()

# ─── SECTION 6: ToolExecutor ────────────────────────────────────
section("6. TOOL EXECUTOR SMOKE TEST")
try:
    from aja.orchestration.tools.executor import ToolExecutor
    te = ToolExecutor()

    # Block test
    blocked = te.execute("mkfs /dev/sda")
    if blocked.get("status") == "error" and "blocked" in blocked.get("message", "").lower():
        print(f"  Block test: PASS")
    else:
        print(f"  Block test: FAIL - got {blocked}")

    # Allow test
    allowed = te.execute(f'"{sys.executable}" -c "print(\'tool_ok\')"')
    if allowed.get("status") == "success" and "tool_ok" in allowed.get("stdout", ""):
        print(f"  Allow test: PASS")
    else:
        print(f"  Allow test: FAIL - got {allowed}")
except Exception as e:
    print(f"  ToolExecutor FAIL: {e}")
    traceback.print_exc()

# ─── SECTION 7: TerminalExec capability ─────────────────────────
section("7. TERMINAL EXEC CAPABILITY")
try:
    from aja.capabilities.terminal import TerminalExec
    from aja.runtime import sandbox
    sandbox._DOCKER_AVAILABLE = False  # Force local
    r = TerminalExec().execute({"cmd": f'"{sys.executable}" -c "print(\'cap_ok\')"', "timeout": 15})
    if r.success:
        print(f"  TerminalExec: PASS mode={r.output.get('mode')} stdout={r.output.get('stdout', '').strip()!r}")
    else:
        print(f"  TerminalExec: FAIL success={r.success} output={r.output}")
except Exception as e:
    print(f"  TerminalExec FAIL: {e}")
    traceback.print_exc()

# ─── SECTION 8: PTY execution ───────────────────────────────────
section("8. PTY EXECUTION CROSS-PLATFORM")
async def test_pty():
    from aja.runtime.execution.manager import ExecutionManager
    from aja.runtime.execution.contracts import ExecutionRequest
    mgr = ExecutionManager()
    req = ExecutionRequest(
        command=f'"{sys.executable}" -c "print(\'pty_ok\')"',
        shell=True,
        use_pty=True,
        timeout=15.0,
    )
    return await mgr.run(req)

try:
    res = asyncio.run(test_pty())
    if res.success:
        print(f"  PTY test: PASS stdout={res.stdout.strip()!r}")
    else:
        print(f"  PTY test: FAIL state={res.state} error={res.error!r} exit_code={res.exit_code}")
        print(f"    stdout={res.stdout!r}")
except Exception as e:
    print(f"  PTY test: EXCEPTION: {e}")
    traceback.print_exc()

# ─── SECTION 9: pywinpty availability ───────────────────────────
section("9. PYWINPTY / PTY AVAILABILITY")
if os.name == "nt":
    try:
        import pywinpty
        print(f"  pywinpty: AVAILABLE version={getattr(pywinpty, '__version__', 'unknown')}")
    except ImportError:
        print(f"  pywinpty: NOT INSTALLED (pty-win extra not installed)")
else:
    print(f"  Platform is POSIX - pywinpty not applicable")

# ─── SECTION 10: Maturin wheel contents ──────────────────────────
section("10. WHEEL CONTENTS CHECK")
import glob
wheels = glob.glob("dist/*.whl")
if not wheels:
    print("  No wheels found in dist/")
else:
    for whl in wheels:
        print(f"  Wheel: {whl}")
        try:
            import zipfile
            with zipfile.ZipFile(whl) as zf:
                names = zf.namelist()
                native = [n for n in names if "aja_native" in n]
                py_src = [n for n in names if n.startswith("aja/") and n.endswith(".py")]
                print(f"    aja_native files: {native}")
                print(f"    Python source files: {len(py_src)} (first 5: {py_src[:5]})")
                # Check for aja.model_providers entry point in METADATA
                dist_info = [n for n in names if n.endswith("METADATA")]
                if dist_info:
                    content = zf.read(dist_info[0]).decode("utf-8", errors="replace")
                    if "aja.model_providers" in content:
                        print(f"    Entry points in wheel METADATA: FOUND aja.model_providers")
                    else:
                        print(f"    Entry points in wheel METADATA: aja.model_providers NOT FOUND")
                entry_points = [n for n in names if n.endswith("entry_points.txt")]
                if entry_points:
                    ep_content = zf.read(entry_points[0]).decode("utf-8", errors="replace")
                    print(f"    entry_points.txt:\n{ep_content}")
        except Exception as e:
            print(f"    ERROR reading wheel: {e}")

# ─── SECTION 11: Maturin build-only wheel name check ────────────
section("11. MATURIN WHEEL NAME CHECK")
# The maturin wheel is named aja_native-0.1.0-cp311-abi3-win_amd64.whl
# But the CI expects aja-0.1.0-*.whl (unified package)
# This is a KEY structural issue
wheels = glob.glob("dist/*.whl")
for whl in wheels:
    base = os.path.basename(whl)
    if base.startswith("aja_native"):
        print(f"  PROBLEM: Wheel is named {base!r}")
        print(f"    => This is aja_native only, NOT the unified aja package!")
        print(f"    => CI install command: pip install 'aja*.whl[all]' will match aja_native wheel")
        print(f"    => but the aja package Python source is NOT bundled in this wheel")
    elif base.startswith("aja-"):
        print(f"  OK: Unified wheel {base!r}")

# ─── SECTION 12: LanceDB / Memory Manager ───────────────────────
section("12. LANCEDB MEMORY MANAGER")
try:
    from aja.memory.manager import get_memory_manager, list_tables_defensive
    mgr = get_memory_manager()
    tables = list_tables_defensive(mgr.db)
    print(f"  LanceDB connected. Tables: {tables}")
    expected = {"core_tasks", "core_tool_executions", "core_plans", "core_triggers"}
    missing = expected - set(tables)
    if missing:
        print(f"  MISSING TABLES: {missing}")
    else:
        print(f"  All expected tables present")
except Exception as e:
    print(f"  LanceDB FAIL: {e}")

# ─── SECTION 13: Replay / Rehydrator ────────────────────────────
section("13. REPLAY SYSTEM CHECK")
try:
    from aja.runtime.execution.rehydrator import EventRehydrator, JournalCorruptionError
    from aja.runtime.event_schema import REDUCERS
    print(f"  EventRehydrator: importable OK")
    print(f"  REDUCERS registered: {list(REDUCERS.keys())[:5]}... ({len(REDUCERS)} total)")
except Exception as e:
    print(f"  Replay FAIL: {e}")
    traceback.print_exc()

# ─── SECTION 14: CI Workflow Analysis ───────────────────────────
section("14. CI WORKFLOW STRUCTURAL ISSUES (STATIC ANALYSIS)")

issues = []

# Issue: maturin build produces aja_native-*.whl but CI expects aja-*.whl
issues.append({
    "severity": "CRITICAL",
    "component": "ci.yml / packaging",
    "issue": "maturin build --release produces aja_native-0.1.0-*.whl, NOT aja-0.1.0-*.whl. "
             "The CI test_runtime job installs with: pip install '${WHEEL}[all]' where "
             "WHEEL=$(ls dist/aja*.whl | head -n 1). This glob matches aja_native but the "
             "unified aja Python package is NOT inside the aja_native wheel. "
             "Result: clean install has NO aja Python source code, only the Rust extension."
})

issues.append({
    "severity": "CRITICAL",
    "component": "pyproject.toml / maturin",
    "issue": "[project.entry-points.'aja.model_providers'] includes copilot = 'aja.llm:CopilotModelProvider' "
             "but the installed wheel's entry_points only shows google/openai/openrouter/llama_cpp. "
             "copilot is missing from the installed distribution - stale wheel from before copilot was added."
})

issues.append({
    "severity": "HIGH",
    "component": "ci.yml build_wheels",
    "issue": "build_wheels job pins python-version: '3.11' but test_runtime matrix includes 3.12. "
             "The abi3-py311 wheel built on 3.11 should load on 3.12, but this is untested in build. "
             "The wheel name aja_native-0.1.0-cp311-abi3-win_amd64.whl may not be picked up by "
             "pip on Python 3.12 if cp311 tag conflicts."
})

issues.append({
    "severity": "HIGH",
    "component": "nightly.yml",
    "issue": "nightly.yml runs maturin develop --release then pip install -e '.[all]'. "
             "This editable install on Python 3.13 is UNTESTED (matrix includes 3.13). "
             "pywinpty, discord.py may not support 3.13 yet."
})

issues.append({
    "severity": "HIGH",
    "component": "Dockerfile (production)",
    "issue": "Dockerfile COPY order: packages/ pyproject.toml first, then libs/. "
             "But maturin build needs BOTH packages/ AND libs/ (python-source = libs/aja-core). "
             "The maturin build in the Dockerfile does include libs/ before building - this is OK. "
             "However the wheel produced will be named aja_native-*.whl not aja-*.whl, so "
             "pip install '${WHEEL}[all]' in Stage 2 will only install the Rust extension, "
             "not the Python source. SAME as CI issue above."
})

issues.append({
    "severity": "MEDIUM",
    "component": "Dockerfile.dev",
    "issue": "Dockerfile.dev doesn't copy source or run maturin develop. "
             "Relies on volume mount at /workspace. If user doesn't run maturin develop manually, "
             "imports will fail inside container."
})

issues.append({
    "severity": "MEDIUM",
    "component": "lib.rs / PyO3 warnings",
    "issue": "Rust build emits 2 warnings: (1) dead_code field model_id in PyTrajectoryManager, "
             "(2) non_local_definitions on #[pymethods] - PyO3 version mismatch warning. "
             "pyo3 = 0.20.3 is pinned while cargo may pull newer pyo3_macros. "
             "Non-local impl warning will be an ERROR in future Rust editions."
})

issues.append({
    "severity": "MEDIUM",
    "component": "ci.yml test_runtime / aja doctor --ci",
    "issue": "aja doctor --ci is run before pytest. The doctor checks Gemini API key and Telegram token. "
             "If these are not set as CI secrets, doctor may emit warnings or exit non-zero "
             "depending on --ci flag behavior. GEMINI_API_KEY was previously hard-coded (now removed), "
             "but if diagnostics.py returns False for any check and --ci flag makes it fatal, "
             "this will block all subsequent tests."
})

issues.append({
    "severity": "MEDIUM",
    "component": "Windows CREATE_NO_WINDOW fix",
    "issue": "The recent fix sets creationflags=subprocess.CREATE_NO_WINDOW on Windows in both "
             "manager.py and transport.py. However, this was the EXACT flag previously removed to "
             "fix a RecursionError. The RecursionError may return on Python 3.12 CI runner. "
             "The actual root cause was CREATE_NEW_PROCESS_GROUP, not CREATE_NO_WINDOW - these are different."
})

issues.append({
    "severity": "LOW",
    "component": "config.py",
    "issue": "find_project_root() references platformdirs BEFORE importing it (line 34 uses it, "
             "import is on line 37). This works due to Python's module-level execution order "
             "since platformdirs is imported at module load before find_project_root is called, "
             "but it's fragile and confusing."
})

for i, issue in enumerate(issues):
    print(f"\n  [{issue['severity']}] {issue['component']}")
    print(f"  {issue['issue']}")

print(f"\n\nTotal issues found: {len(issues)}")
print(f"  CRITICAL: {sum(1 for i in issues if i['severity']=='CRITICAL')}")
print(f"  HIGH:     {sum(1 for i in issues if i['severity']=='HIGH')}")
print(f"  MEDIUM:   {sum(1 for i in issues if i['severity']=='MEDIUM')}")
print(f"  LOW:      {sum(1 for i in issues if i['severity']=='LOW')}")
