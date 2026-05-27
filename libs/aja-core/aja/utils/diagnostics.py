import os
import sys
try:
    import psutil
except ImportError:
    psutil = None
import logging
from pathlib import Path
from typing import List, Tuple

from aja.config import PROJECT_ROOT, DATA_DIR, CONFIG
from aja.memory.manager import get_memory_manager, list_tables_defensive

logger = logging.getLogger("aja.diagnostics")

def run_diagnostics() -> List[Tuple[str, bool, str]]:
    checks = []

    # 1. Config Validation
    config_path = DATA_DIR / "aja.json"
    if not config_path.exists() and (PROJECT_ROOT / "aja.json").exists():
        config_path = PROJECT_ROOT / "aja.json"

    if not config_path.exists():
        checks.append(("Config File", False, f"Missing aja.json in {DATA_DIR}"))
    else:
        try:
            from aja.config_schema import AJAConfig
            import json
            with config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            AJAConfig.model_validate(data)
            checks.append(("Config Validation", True, "aja.json is fully valid against Pydantic schema"))
        except Exception as e:
            checks.append(("Config Validation", False, f"Invalid aja.json: {e}"))

    # 2. Native Rust Engine (aja-native)
    try:
        from aja import aja_native
        has_write = hasattr(aja_native, "write_baton")
        has_read = hasattr(aja_native, "read_baton")
        has_batch = hasattr(aja_native, "PyTrajectoryManager")
        if has_write and has_read and has_batch:
            checks.append(("Native Engine", True, "aja_native extension successfully loaded (PyO3 stable ABI)"))
        else:
            checks.append(("Native Engine", False, "aja_native loaded but missing trajectory or baton serialization functions"))
    except ImportError as e:
        checks.append(("Native Engine", False, f"Failed to load aja_native Rust module: {e}"))

    # 2b. Execution Transport & PTY Capabilities
    try:
        if os.name == "nt":
            try:
                import pywinpty
                has_pty = pywinpty is not None
                pty_msg = "Windows ConPTY active via pywinpty"
            except ImportError:
                has_pty = False
                pty_msg = "Windows ConPTY inactive (pywinpty missing, falling back to PipeTransport)"
        else:
            # POSIX pseudo-terminal is always natively supported by standard library
            has_pty = True
            pty_msg = "POSIX pseudo-terminal natively supported"
        
        try:
            import pyarrow
            has_arrow = True
            arrow_msg = "PyArrow zero-copy buffer mapping supported"
        except ImportError:
            has_arrow = False
            arrow_msg = "PyArrow missing (falling back to standard IPC disk read/write)"

        checks.append(("Execution Transport", True, f"PTY: {pty_msg} | Arrow: {arrow_msg}"))
    except Exception as e:
        checks.append(("Execution Transport", False, f"Diagnostics failed: {e}"))

    # 3. LanceDB & Unified Memory Stack
    try:
        mgr = get_memory_manager()
        tables = list_tables_defensive(mgr.db)
        expected_tables = {"core_tasks", "core_tool_executions", "core_plans", "core_triggers"}
        missing_tables = expected_tables - set(tables)
        
        # Verify schema of core_plans has float list of size 384
        try:
            plans_tbl = mgr.get_table("core_plans")
            schema = plans_tbl.schema
            vector_type = schema.field("vector").type
            if hasattr(vector_type, "value_length") and vector_type.value_length == 384:
                vector_status = "Standard 384D Vector schema verified"
            else:
                vector_status = f"Warning: non-standard vector dimension in core_plans: {vector_type}"
        except Exception as e:
            vector_status = f"Could not verify vector schema: {e}"

        if not missing_tables:
            checks.append(("Memory Manager", True, f"LanceDB active. All tables verified. ({vector_status})"))
        else:
            checks.append(("Memory Manager", False, f"LanceDB active but missing expected tables: {missing_tables}"))
    except Exception as e:
        checks.append(("Memory Manager", False, f"Failed to initialize LanceDB connection: {e}"))

    # 4. LLM & Secrets Configuration
    secrets = []
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        secrets.append("Gemini/Google API Key set")
    else:
        secrets.append("Gemini API Key missing")

    if os.getenv("TELEGRAM_TOKEN"):
        secrets.append("Telegram Token set")
    else:
        secrets.append("Telegram Token missing (remote assistant client disabled)")

    has_keys = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    checks.append(("API & Credentials", has_keys, " | ".join(secrets)))

    # 5. System Resources (CPUs, RAM, Disk)
    try:
        if psutil is not None:
            cpu_count = psutil.cpu_count(logical=True)
            ram = psutil.virtual_memory()
            total_ram_gb = ram.total / (1024 ** 3)
            disk = psutil.disk_usage(str(DATA_DIR))
            free_disk_gb = disk.free / (1024 ** 3)
            sys_details = f"CPUs: {cpu_count} | RAM: {total_ram_gb:.1f} GB | Free Disk: {free_disk_gb:.1f} GB"
            # Check system requirements (minimum 1GB free ram, 2GB disk recommended for local RAG)
            has_min_resources = (ram.available / (1024 ** 3) > 1.0) and (free_disk_gb > 2.0)
        else:
            cpu_count = os.cpu_count() or 1
            import shutil
            disk = shutil.disk_usage(str(DATA_DIR))
            free_disk_gb = disk.free / (1024 ** 3)
            sys_details = f"CPUs: {cpu_count} | RAM: N/A (psutil missing) | Free Disk: {free_disk_gb:.1f} GB"
            has_min_resources = free_disk_gb > 2.0

        checks.append(("System Resources", has_min_resources, sys_details))
    except Exception as e:
        checks.append(("System Resources", False, f"Failed to query system resources: {e}"))

    return checks
