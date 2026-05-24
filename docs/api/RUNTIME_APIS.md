# Runtime APIs

AJA Runtime provides several boundary APIs for integration. To prevent architecture drift, contributors must understand which interfaces are stable and which are internal.

## Public Runtime APIs
These are highly stable interfaces intended for consumption by external clients, gateways, and UI presenters.

- **`RuntimeTaskStore` / `LanceRuntimeTaskStore`** (`aja/runtime/task_store.py`)
  - **Ownership**: Runtime Persistence Layer.
  - **Guarantee**: Schema compatibility. Safe to query via REST adapters or CLI interfaces to display job status.
- **`CronScheduler`** (`aja/scheduler/cron_scheduler.py`)
  - **Ownership**: Runtime Orchestration.
  - **Guarantee**: Methods like `add_job`, `pause_job`, and `list_jobs` are stable public interfaces for managing workflow.
- **`RuntimeEventSink`** (`aja/runtime/events.py`)
  - **Ownership**: Observability Layer.
  - **Guarantee**: Clients can safely poll this sink to build live dashboards or WebSocket streams.

## Semi-Stable APIs
These interfaces are safe to use within the AJA core codebase but should not be directly exposed to external, out-of-process consumers yet.

- **`BatonManager`** (`aja/runtime/handover.py`)
  - The Python class methods (`capture`, `pickup`, `transmit_baton`) are stable, but the underlying PyO3/Apache Arrow schema is brittle and subject to optimization changes.

## Internal-Only APIs
These modules are strictly internal. Touching them requires careful review.

- **`execute_command`** (`aja/runtime/sandbox.py`)
  - Must never be called directly by client interfaces. It is strictly owned by the Orchestration/Worker execution loop. Direct calls bypass security classifiers and trace propagation.
- **`autonomous_loop.py`**
  - This is the main bootstrapper for in-process async workers. It is an internal implementation detail of how local agents are spawned and should not be imported as a library.
