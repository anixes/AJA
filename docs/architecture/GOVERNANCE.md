# Resource Governance & Execution Policy

As an enterprise-grade agentic orchestration engine, the AJA Runtime establishes robust resource limits to prevent runaway workflows, host resource exhaustion, and unauthorized lateral network movement. 

Resource governance is managed by the `GovernancePolicy` subsystem (`libs/aja-core/aja/runtime/execution/governance.py`), ensuring every command invocation conforms to the platform's global execution boundaries.

---

## 1. Global Execution Policy Schema

The global resource limits are defined under the `execution_policy` block in `aja.json` (validated via `config_schema.py`):

| Config Field | Default Value | Description |
|---|---|---|
| `max_timeout` | `300.0` | Absolute hard execution ceiling in seconds per task. |
| `max_memory` | `"1024m"` | Absolute physical memory limit (e.g., `"512m"`, `"2g"`). |
| `max_cpus` | `2.0` | CPU allocation limits (e.g., `2.0` for 2 full cores). |
| `allow_network_default` | `False` | Sandbox network access restriction. |
| `force_docker` | `False` | Force-escalate executions to Docker containers to guarantee hard OS boundaries. |

---

## 2. Policy Resolution & Clamping Semantics

When an `ExecutionRequest` is dispatched to the `ExecutionManager`, it is passed through the `GovernancePolicy` resolver. The resolver enforces hard mathematical maximum bounds, returning a `BoundedExecutionLimits` object:

1. **Timeout**: Clamped to the minimum of the request's requested timeout and the global `max_timeout`.
2. **Memory**: Evaluated in bytes. Requests demanding more than `max_memory` are clamped down to the global limit.
3. **CPUs**: CPU shares are clamped to `max_cpus`.
4. **Network**: If global defaults block network access (`allow_network_default = False`), requested network access is denied.
5. **Docker**: If `force_docker` is globally set to `True`, the execution is forced into a Docker container even if the request did not specify it.

---

## 3. Enforcement Mechanisms

Because AJA Runtime is designed to run across multiple environments, it uses tiered enforcement based on target OS and container availability:

### A. Containerized Enforcement (Docker Mode)
When running in Docker mode (either requested or forced by `force_docker=True`):
- **Memory**: Hard-clamped using `--memory` control flags.
- **CPU**: Allocated via `--cpus` options.
- **Network**: The container is started with isolated network drivers (`--network none` by default) if network access is restricted.
- **Isolation**: Prevents any access to the host's primary file tree.

### B. POSIX Native Enforcement (Local Subprocesses)
On Linux and macOS, AJA uses low-level POSIX process capabilities:
- Enforced using `preexec_fn` via Python's native `resource` module inside the fork lifecycle (`create_posix_preexec_fn`).
- **Memory**: Set using the virtual memory limit (`RLIMIT_AS`), which raises an `OSError` or terminates the process if the memory footprint grows beyond the allocation.
- **CPUs**: CPU time constraints are mapped to standard limits (`RLIMIT_CPU`) to act as a secondary fail-safe.

### C. Windows Native Enforcement (Local Subprocesses)
On Windows, subprocess resource limits exhibit specific behaviors due to platform constraints:
- **Timeout**: Fully enforced via async task timeouts and process tree termination (`taskkill` / recursive handle cleanup).
- **Graceful Degradation**: Memory and CPU ceilings gracefully degrade to timeouts only for host-level subprocesses to avoid loading brittle, unsafe native Windows Job Objects via `ctypes`.
- **Policy Suggestion**: Operators requiring strict multi-dimensional memory and CPU resource governance on Windows should configure `force_docker = True` to guarantee secure container boundaries.

---

## 4. Manifest Persistence

Every execution logs its applied constraints inside the `ExecutionManifest` (`contracts.py`) under the `applied_limits` metadata field. This ensures that:
- The operator can audit exactly what resources were allocated to the task via `aja exec show <session_id>`.
- Replay and visualization tools can display resource bounds and track utilization against the clamped limits.
