# Clients and Gateways

AJA strictly enforces the separation of concerns between the **Runtime Engine** (which owns state and execution) and **Clients/Presenters** (which own user interaction).

## 1. The Client Model
Clients are external consumers of the `RuntimeTaskStore` and `RuntimeEventSink`. They do not "run" tasks; they display them.

Currently implemented clients:
- **CLI (`aja main`)**: Direct terminal interface for queuing tasks.
- **TUI (`curses_tui.py`)**: A rich Curses dashboard for visualizing the HTN plan tree and tailing logs.
- **Telegram (`telegram.py`)**: A chat bridge adapter.

## 2. The Dependency Rule
**The Runtime must never import Client code.**
- `aja/runtime/` cannot know about `telegram.py` or `curses_tui.py`.
- If the Orchestrator needs to inform the user of a decision, it does not send a Telegram message. It emits a payload to the `EventSink`. The Telegram adapter listens to the sink and decides how to present it.

## 3. Legacy Containment
Historically, AJA was built as a chatbot ("Jarvis assistant"), which led to tight coupling between LLM persona prompts and execution logic. 
- Legacy endpoints (like old Dashboards or direct messaging loops) have either been deleted or boxed into the `aja/gateway/` adapters layer.
- If you find LLM "persona" framing inside `aja/runtime/` or `aja/scheduler/`, it is technical debt and should be refactored into a presentation adapter.
