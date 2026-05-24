# AgentX Runtime Boundaries

AgentX is the local-first runtime/infrastructure layer. AJA is a first-party client built on top of it.

This document records the current boundaries after the runtime stabilization and compatibility-preserving API extraction passes.

## Ownership Model

- Runtime owns orchestration semantics, scheduler decisions, worker lifecycle, baton lifecycle, command safety, trace-aware events, and async execution rules.
- Rust owns performance-critical primitives: Arrow IPC baton serialization/deserialization, token counting, and native acceleration helpers.
- AJA and other clients own persona wording, chat UX, Telegram/mobile formatting, executive/task copy, and assistant-specific presentation.
- LanceDB currently stores both runtime and client data. Existing `aja_*` table names are legacy physical names, not ownership boundaries.

## Runtime Contracts

- Scheduler code depends on `RuntimeTaskStore` and `RuntimeEventSink`, not concrete client memory classes.
- Runtime persistence protocols live in `agentx.runtime.store_protocols`.
- The current LanceDB compatibility adapter is `agentx.runtime.lance_stores.LanceRuntimeStore`; it is the only runtime/scheduler/orchestration module allowed to import `get_aja_memory`.
- Baton code depends on `agentx.runtime.handover.BatonManager` as the runtime-owned Arrow IPC boundary.
- Event producers emit dictionaries compatible with `RuntimeEvent`; sinks normalize events before persistence.
- Blocking subprocess, network, and LanceDB work must stay in synchronous APIs or run behind `asyncio.to_thread()` when called from async paths.
- Runtime websocket/event broadcast helpers live in `agentx.runtime.broadcast` so legacy servers remain thin wrappers.

## API Boundary

`agentx.api.bridge` remains the compatibility FastAPI entrypoint. Existing imports and endpoint paths must continue to work.

Route ownership is declared under `agentx.api.routes`:

- `runtime.py`: approvals, runtime events, baton state, streams, swarm run, status snapshots.
- `memory.py`: workers, tasks, communications, priority engine, scheduler review endpoints.
- `telegram.py`: Telegram status/history/command/webhook paths.
- `legacy.py`: dashboard-compatible, config, safety, and websocket paths.

Shared non-route behavior belongs under `agentx.api.services`:

- `command_policy.py`: command safety classification used by bridge/client surfaces.
- `legacy_dashboard.py`: deprecated dashboard launcher response; it must not spawn deleted web assets.

## Orchestration Presentation

Core orchestration should not require AJA persona text.

- `SwarmEngine` accepts a presenter object.
- `agentx.gateway.presenter.NullPresenter` is the neutral runtime presenter.
- `agentx.gateway.presenter.AJAPresenter` owns AJA console wording and direct-execution persona prompts.
- New runtime execution paths should accept a neutral presenter or no presenter; client layers choose AJA presentation when needed.

## Legacy Client Surfaces

These modules remain for compatibility and tests, but should not gain new runtime behavior:

- `agentx.server.mobile_bridge`
- `agentx.server.api`
- `agentx.dashboard.api`
- `agentx.scheduler.scheduler`
- Telegram-specific routes in `agentx.api.bridge`

Legacy modules should:

- expose `LEGACY_CLIENT_SURFACE = True`;
- delegate shared runtime behavior to runtime helpers;
- remain importable until compatibility tests and callers are retired;
- not add new runtime ownership or new autonomous behavior.

## Validation

Run the full suite with the project Python:

```powershell
$env:PYTHONPATH="libs/agentx-core"; & "C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests/python
```

Current boundary guards live in `tests/python/test_runtime_boundaries.py` and verify:

- `agentx.api.bridge.app` still exposes compatibility routes.
- Route ownership metadata exists under `agentx.api.routes`.
- Command policy delegates to the shared command guard.
- Deprecated dashboard launch does not spawn a web app.
- Runtime/scheduler/orchestration modules do not import client memory directly, except `runtime.lance_stores`.
- Async functions in API/server/gateway/scheduler/runtime scopes do not directly call blocking process/network/sleep APIs.

Current runtime stabilization tests live in `tests/python/test_runtime_stabilization.py` and verify:

- CronScheduler cancels active tasks cleanly on async stop and prevents overlapping runs.
- EventBus isolating failures, supporting async execution, and deduplicating subscriptions via `subscribe_once`.
- BatonManager bounded memory caching, thread safety, and schema conformance.
- SessionManager lifecycle management, safe thread synchronization, and clean session resets.
- LanceDBLogger idempotency and correct event normalization.

Last verified result after the cleanup pass:

```text
160 passed
```

## Rule Of Thumb

New runtime behavior lands behind composable runtime APIs first, then client/interface layers adapt it. If a change needs AJA wording, Telegram formatting, mobile response shapes, or dashboard compatibility, that logic belongs in client/gateway/API service layers, not runtime internals.
