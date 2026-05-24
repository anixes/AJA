# Contributing Guidelines

When contributing to the AJA Runtime, you are building infrastructure. Contributions must prioritize deterministic behavior, observability, and strict architectural boundaries.

## 1. Async Safety Rules
AJA heavily utilizes `asyncio` for high-I/O scheduling and orchestration.
- **No Blocking Calls**: Never place blocking operations (e.g., synchronous HTTP requests, heavy JSON parsing, or `time.sleep()`) directly in an `async` function. 
- Use `await asyncio.to_thread(func)` or `asyncio.sleep()` to prevent stalling the main event loop.

## 2. Global Mutable State
- **Prohibited**: Do not introduce new global variables, singletons, or un-locked shared dictionaries. 
- If you need to store state, persist it to the `LanceRuntimeTaskStore`. The *only* exception is the tightly locked `_IN_MEMORY_BATONS` cache in `handover.py`.

## 3. Dependency Rules (The "Client" Rule)
- Code inside `aja/runtime/`, `aja/scheduler/`, or `aja/observability/` **must never** import code from presentation layers like `aja/tui/`, `aja/gateway/`, or client scripts.
- If the runtime needs to output data, emit it to the `RuntimeEventSink`.

## 4. Execution Sandbox Modifications
- Changes to `sandbox.py` require extreme scrutiny.
- Do not bypass Docker bounds unless explicitly designing a validated fallback mechanism.
- Ensure any new execution modes properly flush outputs to the trace telemetry system.

## 5. Avoid Speculative Abstractions
Do not build "frameworks" for hypothetical future AGI agents. Build concrete solutions for the actual execution runner, IPC layer, and scheduler that exist today.

## 6. Tracing is Mandatory
If you add a new subsystem, ensure it inherits the active `trace_id` from the `TraceContextManager` and logs via `telemetry.py`. Silent failures in background workers are unacceptable in a distributed runtime.
