# Phase 18: Hard Sandbox, Versioning & Human-in-the-Loop

This phase adds strict containerized execution, tracks structural changes across replanning events, and brings humans into the critical path.

## Wave 1: Hard Sandbox (Container Isolation) — COMPLETE
1. **Docker Execution (`agentx/runtime/sandbox.py`)**:
   - `execute_command()` routes through Docker when daemon is available.
   - Graceful fallback to direct subprocess when Docker is offline.
   - Project workspace mounted at `/workspace`; resource limits enforced.
2. **Terminal Capability Integration**:
   - `TerminalExec` uses `run_in_sandbox()` which selects Docker or fallback automatically.
   - Execution `mode` (`'docker'` | `'direct_fallback'`) surfaced in output for observability.

## Wave 2: Plan Versioning System — COMPLETE
1. **PlanVersion (`agentx/planning/models.py`)**:
   - Full `@dataclass` with `id`, `parent`, `plan`, `timestamp`, `label`, `iso_timestamp`.
   - `to_dict()` / `from_dict()` for JSON persistence.
2. **VersionStore (`agentx/planning/version_store.py`)**:
   - `cut()` — deep-snapshot the graph and persist to `.agentx/plan_versions/<plan_id>/`.
   - `chain()` / `latest()` / `load()` for retrieval.
3. **ReActExecutor** wires `VersionStore.cut()` at plan start and on every repair.
4. **TraceStore** (`agentx/observability/trace.py`) now records `version_id` per event.

## Wave 3: Human-in-the-Loop Control — COMPLETE
1. **Session (`agentx/runtime/session.py`)**:
   - Added `is_rejected` flag and `reject()` method to unblock executor on rejection.
2. **Event Bus (`agentx/runtime/event_bus.py`)**:
   - Added `AWAITING_APPROVAL`, `NODE_APPROVED`, `NODE_REJECTED` event types.
3. **API (`agentx/server/api.py`)**:
   - `POST /hitl/approve` — approve pending node with optional `input_overrides`.
   - `POST /hitl/reject` — reject with reason; triggers `session.reject()`.
   - `POST /hitl/modify_node` — live-patch any node field while paused.
   - `GET /hitl/status/{user_id}` — inspect pending node + session state.

## Wave 0: Environment Stabilization (COMPLETE)
- [x] **Packaging Fix**: Resolved shadowing conflict between `agentx.py` and `agentx/` package.
- [x] **Path Resolution**: Robust `find_project_root()` centralised in `agentx/config.py`.
- [x] **API Gateway**: Verified connectivity for online/offline/hybrid modes.

---

*Status*: **Phase 18 COMPLETE** — Wave 0, 1, 2, 3 all done. Ready for next phase.

