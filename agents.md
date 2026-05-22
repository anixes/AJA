# Agent Context & Handover Guide (agents.md)

Welcome, fellow agent! This file provides a comprehensive context summary, architectural maps, and recent changes so you can immediately begin developing, testing, or debugging the AgentX & AJA system without friction.

---

## 🎯 System Overview & Scope

**AgentX** is an enterprise-grade agentic orchestration engine designed to manage autonomous multi-agent missions with extreme observability, safety controls, and multi-tenant telemetry.
* **Core Language**: Python (running under **global Python 3.12.10**).
* **Runtime Core**: `libs/agentx-core` containing configuration validation, safe plan simulation (`--dry-run`), systems diagnostics (`doctor`), and the baton handover subsystem.
* **Native Rust Integration**: PyO3 GIL-free IPC engine (`agentx_native`) which serializes execution batons into Apache Arrow format.
* **Unified Memory Stack**: LanceDB vector store initializing schemas for core execution tables.

---

## ⚡ Recent Architecture Upgrades

We recently implemented five core product-readiness enhancements:

### 1. Robust Configuration Control (Pydantic Schema Validation)
* **Schema Definition**: [config_schema.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/config_schema.py) defining models for territories, swarms, models, settings, and agent configs.
* **Import Hook**: Modified [config.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/config.py) to validate `agentx.json` against the schema. If validation fails, it outputs warning details and safely loads strict, secure default settings rather than failing silently or throwing unexpected runtime exceptions.
* **Tests**: Verified under [test_config_validation.py](file:///d:/AgenticAI/Project1(no-name)/tests/python/test_config_validation.py).

### 2. Trace-Aware Observability & Telemetry Context Manager
* **Trace Propagation**: Created `TraceContextManager` in [telemetry.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/observability/telemetry.py). It tracks a thread-local and asyncio-local context variable `_trace_id_ctx`.
* **Arrow IPC Integration**: The `BatonManager` in [handover.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/runtime/handover.py) automatically serializes active `trace_id` values into Arrow metadata headers upon capturing, and recovers/restores them into the current context during pickup.
* **Tests**: Isolation across multiple async task groups (`anyio` backend-agnostic task groups) and threads is tested in [test_trace_telemetry.py](file:///d:/AgenticAI/Project1(no-name)/tests/python/test_trace_telemetry.py).

### 3. Resilient Diagnostics & Systems Doctor
* **Module**: [diagnostics.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/utils/diagnostics.py).
* **Dependency Resiliency**: Made `psutil` an optional soft-dependency. If absent, the diagnostics tool falls back to Python standard library options (`os.cpu_count()` and `shutil.disk_usage()`), preventing `ModuleNotFoundError` crashes.
* **CLI Endpoint**: Run `/doctor` via `agentx doctor` to verify environment readiness (configuration schema, native PyO3 engine, LanceDB tables, API tokens, and minimum memory/disk space).

### 4. Interactive Scaffolding & Setup Wizard
* **Onboarding Tool**: Built `agentx setup` inside [main.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/main.py), utilizing rich-prompts to scaffold the standard workspace layout.

### 5. Safe Plan Simulation (Dry-Run Mode)
* **Goal**: Simulates plans, audits potential shell executions against `AJAGuard` safety blocks, and tracks step transitions safely under the active trace ID without mutating any local files or running live commands.
* **CLI Flag**: Run commands using `--dry-run` to preview swarms safely.

---

## 🚀 Phase 1 Architectural Upgrades & Web UI Deprecation

We recently completed the Phase 1 Architectural Upgrades, transitioning AgentX into a pure, local-first terminal CLI/TUI assistant:

### 1. Pluggable Model and Tool Interfaces (ABCs)
* **Decoupled Extensions**: Created `BaseModelProvider` and `BaseTool` abstract base classes in [interfaces.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/api/interfaces.py).
* **Dynamic Interfacing**: Rebuilt the LLM completion gateway to dynamically register custom model provider subclasses via `ModelProviderRegistry`.

### 2. Multi-Interface Async Gateway
* **Session Broker**: Built [gateway_runner.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/gateway/gateway_runner.py) to manage concurrent multi-platform communication adapters.
* **Adapters**: Integrated native asynchronous adapters for Discord socket mode and Slack SocketMode, enabling continuous multi-interface chat sessions.

### 3. Persisted Cron Task Scheduler
* **LanceDB Cron**: Built [cron_scheduler.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/scheduler/cron_scheduler.py) to parse standard 5-field cron strings (`* * * * *`) and recurring intervals (`every 30s`), preserving job state inside LanceDB databases.
* **Timeout Shield**: Enforces a strict 3-minute timeout limit using `asyncio.wait_for` on scheduled swarm execution turns, preventing resource exhaustion from runaway planning loops.

### 4. Curses Live HTN Dashboard (Terminal TUI)
* **Bulletproof Viewport Layout**: Replaced the cluttered browser GUI with a premium local Curses terminal TUI dashboard in [curses_tui.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/tui/curses_tui.py) containing a live HTN Plan tree DAG, tailing logs view, and metrics panel.
* **Dynamic Styling**: Features three data-driven theme skins: `cyberpunk` (cyan/magenta neon grid), `ares` (tactical amber/red), and `default` (sleek gray) toggleable on the fly with the `s` hotkey.

### 5. Distributed Arrow Baton Handover Fleet
* **Network-Scalable IPC**: Extended [handover.py](file:///d:/AgenticAI/Project1(no-name)/libs/agentx-core/agentx/runtime/handover.py) with base64 Apache Arrow serializers.
* **Multi-Host Orchestration**: Supports transmitting and picking up high-performance GIL-free execution batons across remote hosts using standard network protocols.

### 6. Transition to Pure Local-First Assistant
* **Web UI Deprecation**: Completely deleted the `apps/dashboard` Vite/React directory to enforce a pure local CLI/TUI experience.
* **Eradicated Legacy Endpoints**: Removed the `/dash` and `dash` CLI subcommand from `main.py` and stripped root `package.json` scripts.
* **Simplified Process Launcher**: Updated `tools/start-aja.mjs` to launch only the core AJA Gateway and autonomous swarms.

### 7. Conversational Butler & Proactive Secretary Persona (AJA the Hacker Butler)
* **Polite & Proactive Conversational Flow**: Upgraded LLM prompts in `libs/agentx-core/agentx/api/bridge.py` and `libs/agentx-core/agentx/gateway/orchestrator.py` to follow a premium hacker-butler secretary persona. AJA communicates with elite developer fluency, polite but proactive phrasing, and witty, concise updates.
* **Proactive Scheduling & Obligation Management**: Capable of planning meetings, organizing schedules, and tracking pending items/obligations automatically.
* **Structural Developer Briefings**: Formats daily and task briefings beautifully and structurally, providing you with quick executive summaries.
* **Safe Intent Parsing Integration**: Refactored the core intent parser prompt in `libs/agentx-core/agentx/interface/intent_parser.py` to match the butler persona while strictly preserving the JSON output schema (`type`, `goal`, `command`, `response`, `confidence`) required for code mapping.

---

## 🚀 Phase 1.2 & 1.3 Upgrades: Interactive Pairing & Autonomy Performance

We recently engineered several critical upgrades to the pairing workflow, performance bottlenecks, and autonomy loops:

### 1. Direct In-Process Execution Mode
* **Direct Execution**: Added the `direct_execution` settings flag which allows running developer/system tasks synchronously in-process via `ToolExecutor` under robust `CommandGuard` security constraints, completely bypassing heavy orchestrator/baton loops for local shell workflows.
* **Proactive Butler Mappings**: Upgraded `cmd_chat()` to map intent patterns directly to local commands (`doctor`, `logs`, `gpu`) with real-time feedback loops.

### 2. Multi-Turn Context Aware Conversation Memory
* **State Awareness**: The interactive `agentx` CLI chat loop now maintains a rolling 15-turn conversation history fed directly to the intent parser, enabling fully contextual multi-step dialogues and resolving memory resets.

### 3. Diversity Plan Generator & Temperature Scaling
* **Diversity Scaling**: Refactored the candidate plan generator (`generator.py`) to systematically supply previously generated plans back into the LLM history, scaling temperatures dynamically via `temp = min(1.0, 0.3 + (attempts * 0.15))` to prevent duplicative plans and infinite loop regenerations.

### 4. Zero-Copy Baton Handover IPC Cache
* **Baton RAM Buffer**: Implemented an in-memory Arrow Baton serialization buffer (`_IN_MEMORY_BATONS` with standard threading locks) inside `handover.py` to cache batons in RAM, enabling sub-millisecond zero-copy transfers while maintaining disk-fallback durability.

### 5. High-Performance Low-Latency Autonomous Startup
* **Synchronous Bootstrap Heartbeat**: Relocated initial heartbeat publishing to happen synchronously at loop startup in `autonomous_loop.py` before loading heavy libraries, correcting worker gateway connectivity issues.
* **Lazy Embedding Model Loading**: Shifted `SentenceTransformer` loading in `EmbeddingService` to be entirely deferred (lazy-loaded) on the first `embed()` call, speeding up imports and CLI response times while exposing an `embed_text()` alias for backwards compatibility.

---

## 🛠️ Verification & Run Commands

When running or testing in this workspace, **always use the project's global Python 3.12.10 installation** located at:
`C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe`

### 1. Execute Python Unit Tests
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests/python
```
* **Result**: **145 passed, 1 warning** (including TUI, cron scheduler, zero-copy baton IPC, plan diversity generator, and direct-in-process execution test coverage).


### 2. Run Curses TUI Live Dashboard (Local Demo)
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m agentx tui --dry-run
```

### 3. Run Diagnostics doctor Checks
```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m agentx doctor
```

### 4. Run Swarm safe Simulation
```powershell
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m agentx run "Perform project analysis" --dry-run
```


---

## 📌 Development Tips for Future Agents

* **Trio & Async Event Loop Warning**: When editing async tasks in telemetry or bridge modules, avoid standard `asyncio.gather` inside test runs. Use backend-agnostic `anyio.create_task_group()` and `tg.start_soon()` to prevent event loop mismatch errors.
* **Soft-Dependencies**: Keep external dependencies lightweight. If adding system metrics or integration packages, import them dynamically or handle missing installations gracefully with standard library alternatives.
* **Git Status Integrity**: Keep code clean. Avoid leaving behind unhandled temporary baton files. Temporary simulation batons should be written in `libs/agentx-core/temp_batons` or the local system tmp directory.
