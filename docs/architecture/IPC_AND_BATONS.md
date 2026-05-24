# IPC and Batons

A fundamental architectural principle of AJA Runtime is the use of **Batons** to transfer execution state. This completely decouples the orchestration memory from the execution environment.

## 1. What is a Baton?
A Baton (`MissionBatonPayload`) is a strictly defined, serializable snapshot of the current agent's state. It includes:
- The objective/goal.
- The multi-turn conversation history.
- The `trace_id` for observability lineage.

## 2. Apache Arrow and PyO3 Integration
Moving massive context windows between worker processes via standard JSON over sockets is prohibitively slow and incurs heavy memory duplication.

To solve this, AJA uses **Apache Arrow** via a Rust/PyO3 integration (`aja-native`).
- `BatonManager.capture()` (`aja/runtime/handover.py`) sends the Python dictionary through the native boundary.
- Rust serializes the state into an Arrow Table and writes it to disk (`.arrow`).
- Crucially, it also caches the Arrow Buffer in RAM (`_IN_MEMORY_BATONS`).
- When a worker needs the state, `BatonManager.pickup()` utilizes `pyarrow.memory_map()`.

**Result**: Zero-copy, O(1) state retrieval. The worker reads the exact memory pages written by the orchestrator without duplicating the massive JSON history string in RAM.

## 3. State Ownership
Once a baton is captured, it represents an immutable snapshot of time. If a worker modifies its local state, it must `capture()` a *new* baton to hand back to the orchestrator.

## 4. The Remote Baton Protocol
For distributed workers, batons are downgraded to base64-encoded Arrow buffers and transmitted via HTTP POST. The receiving host writes the `.arrow` file and immediately caches it in its own `_IN_MEMORY_BATONS` dictionary.
