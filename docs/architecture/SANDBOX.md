# Sandbox & Execution Environment

The sandbox boundary is now owned by `aja.runtime.execution`. Legacy helpers such as `aja.runtime.sandbox.execute_command()`, `TerminalExec`, and `ToolExecutor` are compatibility wrappers over the canonical `ExecutionManager`.

## Execution Roots

Commands run in an isolated local workspace by default.
- In a git repository, AJA creates a detached git worktree under `.aja/workspaces/<session_id>`.
- If git worktrees are unavailable, AJA falls back to a temporary workspace copy.
- Execution logs, manifests, and artifacts are stored under `.aja/executions/<session_id>`.
- The source workspace is not merged automatically. Operators must inspect the generated diff before applying changes.

## Docker Mode

When Docker is available, the container mounts the isolated execution root, not the live project root.
- Network defaults to disabled.
- Memory and CPU flags are still applied.
- Docker is a stronger process boundary than the local fallback, but it is not treated as a complete enterprise sandbox.

## Local Mode

When Docker is unavailable, commands run as host subprocesses inside the isolated workspace.
- This protects the live repo from normal filesystem mutations.
- It does not prevent host-level side effects outside the workspace if a command intentionally targets absolute paths or external services.
- Command guard checks still run before execution.

## Streaming And PTY Behavior

AJA emits line-buffered stdout/stderr execution events and persists both streams incrementally. Windows uses async subprocess streaming. POSIX systems are prepared for PTY-style execution semantics, but stderr separation remains best-effort because real PTYs generally multiplex streams.

## Cleanup Guarantees

On timeout or cancellation, AJA attempts graceful process-tree termination, then forced cleanup. If `psutil` is installed, child process tracking is recursive; otherwise AJA falls back to platform process-group termination. Workspace cleanup failures are recorded as execution telemetry.
