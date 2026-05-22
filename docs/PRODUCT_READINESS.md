# Product-Readiness Upgrades
### *AgentX Phase 29 — Enterprise Hardening (2026-05-20)*

This document provides a deep technical reference for the five product-readiness upgrades that bring AgentX to enterprise-grade quality, observability, and developer experience.

---

## Overview

| # | Capability | Module | CLI Command | Tests |
|---|---|---|---|---|
| 1 | Pydantic Config Validation | `agentx/config_schema.py` + `config.py` | — (auto on import) | `test_config_validation.py` |
| 2 | Trace-Aware Observability | `agentx/observability/telemetry.py` + `runtime/handover.py` | — (always active) | `test_trace_telemetry.py` |
| 3 | Systems Diagnostics Doctor | `agentx/utils/diagnostics.py` | `agentx doctor` | — |
| 4 | Guided Setup Wizard | `agentx/main.py` | `agentx setup` | — |
| 5 | Safe Dry-Run Simulation | `agentx/orchestration/swarm.py` | `agentx run "..." --dry-run` | — |

---

## 1. Pydantic Configuration Validation

### Problem
`agentx.json` misconfigurations (typos, wrong types, missing required keys) caused silent runtime failures — often discovered only deep inside a running swarm worker, making debugging painful.

### Solution
A strict Pydantic v2 schema validates the full config on every import. On validation failure, a clear, field-level warning is printed and the system falls back to secure, pre-hardcoded defaults — no crash, no silent corruption.

### Key Files
- **[config_schema.py](../libs/agentx-core/agentx/config_schema.py)**: Defines `TerritoryConfig`, `SwarmModels`, `SwarmSettings`, `AgentXConfig`.
- **[config.py](../libs/agentx-core/agentx/config.py)**: Imports and validates `agentx.json` on module load.

### Validation Flow
```
agentx.json (on disk)
    → json.load()
    → AgentXConfig.model_validate(data)
    → ✅ Config accepted  OR  ⚠️ Warning printed + safe defaults loaded
```

### Test Coverage
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests/python/test_config_validation.py -v
```

---

## 2. Trace-Aware Observability

### Problem
Multi-agent missions dispatch work across threads, async tasks, and Rust-native Arrow Baton IPC. Without trace correlation, failures could not be attributed to specific mission runs or workers — observability was effectively blind.

### Solution
A `contextvars`-local `TraceContextManager` propagates trace IDs cleanly across:
- **Threads**: Each thread gets its own isolated trace context via `contextvars.copy_context()`.
- **Async tasks**: `anyio` task groups inherit isolated contexts per task.
- **Arrow Baton IPC**: `BatonManager.capture()` embeds the active `trace_id` in Arrow metadata headers; `BatonManager.pickup()` restores it into the current context automatically.

### Key Files
- **[telemetry.py](../libs/agentx-core/agentx/observability/telemetry.py)**: `TraceContextManager`, `get_trace_id()`, `set_trace_id()`, `_trace_id_ctx`.
- **[handover.py](../libs/agentx-core/agentx/runtime/handover.py)**: `BatonManager` with Arrow metadata trace embedding.

### API
```python
from agentx.observability.telemetry import TraceContextManager, get_trace_id

# Wrap a block of work in a named trace
with TraceContextManager("tr-abc123") as trace_id:
    print(get_trace_id())  # "tr-abc123"
    # dispatch workers, capture batons — trace_id follows automatically

# After the block, the previous trace context is restored
```

### Trace Round-Trip via Arrow Baton
```
TraceContextManager("tr-abc123")
    → BatonManager.capture()  →  Arrow file (.arrow) with metadata: {"trace_id": "tr-abc123"}
    → context reset
    → BatonManager.pickup()   →  trace_id restored to "tr-abc123" in current context
```

### Test Coverage
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests/python/test_trace_telemetry.py -v
```

---

## 3. Systems Diagnostics Doctor

### Problem
Environment misconfigurations (wrong API key variable name, LanceDB schema mismatch, missing Rust module) were discovered at runtime in the middle of a mission — too late and too noisy.

### Solution
`agentx doctor` runs a structured pre-flight health check across 5 subsystems and prints a formatted Rich table. Failures are clearly labelled with actionable messages.

### Key File
- **[diagnostics.py](../libs/agentx-core/agentx/utils/diagnostics.py)**

### Checks Performed
| Check | What It Verifies |
|---|---|
| Config Validation | `agentx.json` passes Pydantic schema |
| Native Engine | `agentx_native` Rust module loads with `write_baton` + `read_baton` |
| Memory Manager | LanceDB connects + all 4 expected tables exist + core_plans vector = 384D |
| API & Credentials | `GEMINI_API_KEY` / `GOOGLE_API_KEY` + `TELEGRAM_TOKEN` present |
| System Resources | CPU count, free RAM (>1GB), free disk (>2GB) |

### Soft Dependency Design
`psutil` is imported with a graceful fallback:
```python
try:
    import psutil
except ImportError:
    psutil = None  # falls back to os.cpu_count() and shutil.disk_usage()
```
This means `agentx doctor` works on any clean Python 3.12 environment without pre-installing psutil.

### Run Command
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m agentx doctor
```

### Sample Output
```
AgentX Diagnostics
 OK  Config Validation:  agentx.json is fully valid against Pydantic schema
 OK  Native Engine:      agentx_native extension successfully loaded (PyO3 GIL-free)
 OK  Memory Manager:     LanceDB active. All tables verified. (Warning: non-standard
                         vector dimension in core_plans: fixed_size_list<item: float>[384])
 OK  API & Credentials:  Gemini/Google API Key set | Telegram Token set
 OK  System Resources:   CPUs: 8 | RAM: N/A (psutil missing) | Free Disk: 22.4 GB
```

---

## 4. Guided Setup Wizard

### Problem
New developers joining the project had no clear path to configure workspace settings, API keys, and LanceDB initialization from scratch — setup was entirely undocumented and manual.

### Solution
`agentx setup` provides an interactive `rich`-prompt guided wizard that walks through:
1. Checking for an existing `agentx.json` and offering to create or overwrite it.
2. Prompting for required API keys and writing them to `.env`.
3. Initializing the `.agentx/` storage directory and LanceDB layout.
4. Running a mini-doctor check at the end to confirm the setup worked.

### Key File
- **[main.py](../libs/agentx-core/agentx/main.py)** — `setup` subcommand handler.

### Run Command
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m agentx setup
```

---

## 5. Safe Dry-Run Simulation

### Problem
Operators had no way to preview what an autonomous mission would do before committing to live execution — especially important for missions involving shell commands, file mutations, or API calls.

### Solution
`--dry-run` flag activates a full simulation mode:
- Plans are generated (or mocked if LLM is offline).
- Every potential shell command is audited through `AJAGuard` safety classification (`ALLOW / ASK / DENY` with risk level).
- Batons are created and "dispatched" to simulated workers — no real execution.
- Results are logged with `[DRY-RUN SIMULATION]` prefix.
- Active `trace_id` is tracked throughout, so dry-run sessions are fully observable.

### Resilience
If the LLM API is unavailable (invalid key, rate limit, offline), the dry-run automatically falls back to a safe locally-generated mock plan rather than crashing.

### Key File
- **[swarm.py](../libs/agentx-core/agentx/orchestration/swarm.py)** — `dry_run` parameter in `SwarmEngine`.

### Run Command
```powershell
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m agentx run "Perform project analysis" --dry-run
```

### Sample Output
```
🐝 Orchestrating Autonomous Objective: Perform project analysis
[DRY-RUN] Simulating tool planning and verification. No physical system changes will be made.
  - Dispatching Worker worker-1: Mock analysis: Perform project analysis
  [DRY-RUN SIMULATION] Simulating worker execution for baton: 'baton_worker-1_xxxx.arrow'
Final Synthesis Complete:
### Swarm Simulation Executive Summary
Status: Simulation Completed Successfully (Dry-Run Mode)
```

---

## Verification

Run the full test suite under global Python 3.12:
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests/python -v
```

**Expected**: `119 passed, 1 warning` (pytest-9.0.3, Python 3.12.10)

> [!NOTE]
> One warning is expected: `DeprecationWarning: There is no current event loop` in `mobile_bridge.py:68`. This is a known upstream issue with `asyncio.get_event_loop_policy().get_event_loop()` on Python 3.12 and does not affect correctness.

---

## Development Guidelines

| Rule | Detail |
|---|---|
| Python Runtime | Always use `C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe` |
| Async in Tests | Use `anyio.create_task_group()`, not `asyncio.gather()` |
| Soft Dependencies | Import optional packages with `try/except ImportError` + stdlib fallback |
| Temp Batons | Write to `libs/agentx-core/temp_batons/` or system tmp |
| Security Audit Log | Written to `.agentx/security_audit.log` as JSON lines |

---
*Generated 2026-05-20 — Phase 29 Product-Readiness Upgrades.*
