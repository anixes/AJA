import os
import sys
try:
    import psutil
except ImportError:
    psutil = None
import logging
from pathlib import Path
from typing import List, Tuple

from agentx.config import PROJECT_ROOT, CONFIG
from agentx.memory.manager import get_memory_manager, list_tables_defensive

logger = logging.getLogger("agentx.diagnostics")

def run_diagnostics() -> List[Tuple[str, bool, str]]:
    checks = []

    # 1. Config Validation
    config_path = PROJECT_ROOT / "agentx.json"
    if not config_path.exists():
        checks.append(("Config File", False, f"Missing agentx.json in project root: {PROJECT_ROOT}"))
    else:
        try:
            from agentx.config_schema import AgentXConfig
            import json
            with config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            AgentXConfig.model_validate(data)
            checks.append(("Config Validation", True, "agentx.json is fully valid against Pydantic schema"))
        except Exception as e:
            checks.append(("Config Validation", False, f"Invalid agentx.json: {e}"))

    # 2. Native Rust Engine (agentx-native)
    try:
        import agentx_native
        has_write = hasattr(agentx_native, "write_baton")
        has_read = hasattr(agentx_native, "read_baton")
        if has_write and has_read:
            checks.append(("Native Engine", True, "agentx_native extension successfully loaded (PyO3 GIL-free)"))
        else:
            checks.append(("Native Engine", False, "agentx_native loaded but missing write/read functions"))
    except ImportError as e:
        checks.append(("Native Engine", False, f"Failed to load agentx_native Rust module: {e}"))

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
        secrets.append("Telegram Token missing (Remote AJA secretary disabled)")

    has_keys = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    checks.append(("API & Credentials", has_keys, " | ".join(secrets)))

    # 5. System Resources (CPUs, RAM, Disk)
    try:
        if psutil is not None:
            cpu_count = psutil.cpu_count(logical=True)
            ram = psutil.virtual_memory()
            total_ram_gb = ram.total / (1024 ** 3)
            disk = psutil.disk_usage(str(PROJECT_ROOT))
            free_disk_gb = disk.free / (1024 ** 3)
            sys_details = f"CPUs: {cpu_count} | RAM: {total_ram_gb:.1f} GB | Free Disk: {free_disk_gb:.1f} GB"
            # Check system requirements (minimum 1GB free ram, 2GB disk recommended for local RAG)
            has_min_resources = (ram.available / (1024 ** 3) > 1.0) and (free_disk_gb > 2.0)
        else:
            cpu_count = os.cpu_count() or 1
            import shutil
            disk = shutil.disk_usage(str(PROJECT_ROOT))
            free_disk_gb = disk.free / (1024 ** 3)
            sys_details = f"CPUs: {cpu_count} | RAM: N/A (psutil missing) | Free Disk: {free_disk_gb:.1f} GB"
            has_min_resources = free_disk_gb > 2.0

        checks.append(("System Resources", has_min_resources, sys_details))
    except Exception as e:
        checks.append(("System Resources", False, f"Failed to query system resources: {e}"))

    return checks
