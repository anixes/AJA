# Phase 18: Hard Sandbox, Versioning & Human-in-the-Loop

This phase adds strict containerized execution, tracks structural changes across replanning events, and brings humans into the critical path.

## Wave 1: Hard Sandbox (Container Isolation)
1. **Docker Execution (`agentx/runtime/sandbox.py`)**:
   - Upgrade the `execute_command` function to route through Docker:
     `docker run --rm --network=none --read-only --cpus=0.5 -m=256m ...`
   - Enforce resource limits (CPU, memory, timeout).
2. **Terminal Capability Integration**:
   - Ensure `TerminalExec` leverages this hard sandbox instead of direct host execution.

## Wave 2: Plan Versioning System
1. **PlanVersion Model (`agentx/planning/models.py`)**:
   - Create a `PlanVersion` object linking `plan`, `parent` version, and `timestamp`.
2. **Version Tracking (`agentx/planning/react_executor.py` & `replanner.py`)**:
   - When the graph is modified during subtree repair, a new `PlanVersion` is cut.
3. **Storage & Replay (`agentx/observability/trace.py`)**:
   - Integrate version ID into the trace logger for accurate playback of evolving plans.

## Wave 3: Human-in-the-Loop Control
1. **Risk Gates (`agentx/planning/react_executor.py`)**:
   - Add a `risk` score to `PlanNode`.
   - If `node.risk > threshold`, emit `AWAITING_APPROVAL` and pause execution.
2. **API Control (`agentx/server/api.py`)**:
   - Expose endpoints: `POST /approve`, `POST /reject`, `POST /modify_node`.
3. **Session State Updates**:
   - Allow modifications to node inputs dynamically while paused.

## Wave 0: Environment Stabilization (COMPLETE)
- [x] **Packaging Fix**: Resolved shadowing conflict between `agentx.py` and `agentx/` package.
- [x] **Path Resolution**: Robust `find_project_root()` implemented in CLI and TUI.
- [x] **API Gateway**: Verified connectivity for online/offline/hybrid modes.

---

*Status*: Wave 0 Complete. Ready to begin Wave 1: Hard Sandbox (Docker Integration).

