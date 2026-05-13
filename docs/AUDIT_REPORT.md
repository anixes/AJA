# Agent Core Surgical Audit: Historical Refactor Summary

This document is a historical record of the architectural refactoring performed before the Assistant Telegram remote-control work.

For the current product phases, see [PHASE_1_2_REMOTE_APPROVALS.md](./PHASE_1_2_REMOTE_APPROVALS.md):

- Phase 1: Telegram Remote Control
- Phase 2: Production Approval Workflow

The sections below use **Audit Stage** labels to avoid confusion with the current Assistant Phase 1 and Phase 2 track.

## Audit Stage 1: Critical Bug Fixes & Token Efficiency

### Critical Bug Fixes (P0)
1.  **Self-Healing Logic Fixes**: Refactored `scripts/self_healer.py` to accurately scan local "territories" rather than relying on hardcoded failure loops. Updated `scripts/health_check.py` to allow dynamic path evaluation.
2.  **Swarm Orchestration Argument Passing**: Patched `scripts/swarm_launcher.py` before it was deprecated in Audit Stage 2 to properly pass valid CLI arguments (`--key`, `--model`, `--prompt`), ensuring sub-agents successfully communicated with the AI Gateway.
3.  **Mock Engine Security**: Refactored `src/engine/QueryEngine.ts` and `src/engine/MockQueryEngine.ts` to use `protected` members instead of unsafe `(this as any)` type-casting in TypeScript.

### Token Efficiency (Audit Stage 1)
1.  **History Sliding Window**: Implemented a token-saving sliding window in `QueryEngine.ts` (pruning the message history to the last 12 messages).
2.  **Dynamic Token Savings Logic**: Replaced hardcoded token-savings metrics with an actual runtime calculation based on the pruned history length.

### API & Shell Hardening
1.  **CORS Restriction**: Tightened `scripts/api_bridge.py` CORS origins to strictly allow local development ports (`3000`, `5173`) and stripped out the wildcard `*`.
2.  **Async I/O in API Bridge**: Wrapped the `build_runtime_snapshot` method in `asyncio.to_thread` to prevent blocking the Server-Sent Events (SSE) telemetry stream.
3.  **Portability**: Switched hardcoded Python paths across all scripts (e.g. `D:\ANACONDA...`) to use dynamic system paths via `sys.executable`.

---

## Audit Stage 2: Swarm Unification & God Node Decoupling

### 1. Unified Gateway Provider Management
We identified 3 different implementations with drifting provider URL maps (`src/services/gatewayClient.ts`, `scripts/gateway.py`, and `scripts/proxy_server.py`).
-   Created a single source of truth at `providers.json`.
-   Refactored all three gateway clients (both Python and TypeScript) to dynamically load the URL map from `providers.json`, preventing future mapping divergence.

### 2. God Node Decoupling (Structural Cleanup)
The `UnifiedGateway` and `CommandStripper` scripts were highly-coupled "God Nodes" cluttering the main `scripts/` directory.
-   Migrated `gateway.py` and `stripper.py` to a dedicated `scripts/core/` library module.
-   Updated all cross-dependencies in dependent scripts (e.g., `agent_worker.py`, `tui_shell.py`, `self_healer.py`) using safe module paths (`from scripts.core.gateway import UnifiedGateway`).

### 3. Merging the Swarm Orchestrators
There were three overlapping orchestration nodes (`swarm_controller.py`, `swarm_launcher.py`, and `baton_orchestrator.py`).
-   Consolidated all three into a single cohesive class within `scripts/swarm_engine.py`.
-   The new `SwarmEngine` supports three standardized CLI execution modes:
    -   `--mode background` (replaces `swarm_controller.py` - persistent territory monitoring)
    -   `--mode parallel` (replaces `swarm_launcher.py` - ThreadPool batch processing)
    -   `--mode baton` (replaces `baton_orchestrator.py` - JSON-based objective breakdown and delegation)
-   Removed the old, deprecated orchestrator files to drastically clean up the codebase.

### Audit Stage 3: Security Hardening
- **CSRF Protection**: Added `verify_token` middleware (dependency-injected) to `api_bridge.py` for `/runtime/approve` and `/runtime/deny` endpoints.
- **Dashboard Integration**: Updated `dashboard/src/App.tsx` to handle authentication tokens when calling `/runtime/approve` and `/runtime/deny`.
