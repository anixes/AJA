# Phase 17: Security, Observability & Control

This phase transforms Agent into a **safe, auditable, controllable runtime system**.

## Wave 1: Security Layer (Permissions + Sandbox)
1. **Permissions Module (`agent/security/permissions.py`)**:
   - `Permission` class: Defines `allowed_capabilities` and `blocked_commands`.
   - Methods `allow(tool)` and `validate_command(cmd)`.
2. **Terminal Sandboxing (`agent/runtime/sandbox.py`)**:
   - Implement `is_safe(cmd)` to block `rm -rf`, `shutdown`, etc.
   - Enhance the terminal capability to validate against this sandbox.
3. **Capability Enforcement**:
   - Update execution layers to throw `PermissionError` if an operation is forbidden.
   - Enforce idempotency if defined in policies.

## Wave 2: Observability Layer (Trace + Replay + Metrics)
1. **Trace Logger (`agent/observability/trace.py`)**:
   - Create `TraceStore` to collect execution events.
   - Save to disk in JSON format (`plan_id`, `node_id`, `event`, `state`, `timestamp`).
2. **EventBus Integration**:
   - Hook `TraceStore` into the `EventBus` so every event is persisted automatically.
3. **Replay Engine (`agent/observability/replay.py`)**:
   - Build a replay function to step through past traces.
4. **Metrics System**:
   - Track key metrics: `success_rate`, `rollback_count`, `repair_rate`, `avg_latency`.

## Wave 3: Interruptible Execution (Pause / Resume / Modify)
1. **Session Control Updates (`agent/runtime/session.py`)**:
   - Extend session state with `checkpoint` and pause functionality.
2. **API Updates (`agent/server/api.py`)**:
   - Expand endpoints: `/interrupt` (already drafted), `/resume`, `/modify`.
3. **Executor Loop Updates (`agent/planning/react_executor.py`)**:
   - Introduce wait locks for `session.is_interrupted`.
   - Combine all prior logic: permissions, verification, transient retries, subtree repair, forward compensation, and trace recording into the ultimate execution loop.

---

*Status*: Ready to begin Wave 1.
