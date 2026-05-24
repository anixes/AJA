# The AJA Runtime

The "Runtime" in AJA refers to the core underlying infrastructure that manages task persistence, scheduling, and observability, completely separate from the AI/orchestration logic.

## 1. The Scheduler (`cron_scheduler.py`)

AJA treats agentic workflows as standard scheduled compute. The `CronScheduler` is the heart of the background execution model.

- **Storage**: Jobs are written to the `RuntimeTaskStore` (LanceDB) with statuses like `scheduled` or `scheduled_paused`.
- **Tick Loop**: An `asyncio` background loop polls the store, evaluating 5-field cron strings or duration expressions (`every 2h`).
- **Execution**: When a job is due, it spawns an isolated `asyncio.create_task` wrapper that invokes the `SwarmEngine`.
- **Hard Interrupts**: To prevent runaway LLM loops or stalled shell commands, the scheduler enforces a strict timeout constraint (currently 3 minutes via `asyncio.wait_for`). If the orchestrator does not yield, it is violently interrupted and marked as failed.

## 2. Persistence Model (`task_store.py`)

The runtime uses **LanceDB** as the source of truth for task state.

- **Schema Validation**: All payloads must map to Pydantic schemas defined in `config_schema.py`.
- **Durability**: Because state is written to LanceDB before execution, if the Python process crashes, tasks are safely recovered on the next boot.
- **Metadata Mutability**: Running tasks lock their state by injecting `active_run_id` and `active_trace_id` into the JSON metadata payload, preventing duplicate worker execution.

## 3. The Event Sink (`event_bus.py` & `telemetry.py`)

The runtime decouples execution from presentation using an Event Sink.

- Code inside the orchestration or worker loops **must not** `print()` directly to the user interface.
- Instead, they emit structured events (`SCHEDULER_JOB_START`, `NODE_FAILED`) containing a `trace_id`.
- The CLI or TUI clients attach listeners to the Event Sink to tail these events and render them to the user.
