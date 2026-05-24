# Canonical Execution Model

Runtime command execution is owned by `aja.runtime.execution.ExecutionManager`. Orchestration, scheduler, CLI, TUI, and mobile clients may request execution, but they do not own subprocess semantics.

## Lifecycle

1. A caller creates an `ExecutionRequest`.
2. `ExecutionManager.start()` creates an `ExecutionSession` with a trace-correlated session ID.
3. `WorkspaceManager` prepares an isolated execution root.
4. The process backend starts the command and records root PID metadata.
5. Stdout and stderr are streamed line-by-line to logs and runtime events.
6. Timeout or cancellation triggers graceful process-tree termination, followed by forced cleanup.
7. A workspace diff and artifact inventory are captured.
8. The isolated workspace is cleaned up.
9. `ExecutionResult`, `manifest.json`, `timeline.jsonl`, `stdout.log`, `stderr.log`, and `workspace_diff.json` remain under `.aja/executions/<session_id>`.

## Compatibility

The old sandbox and terminal capability APIs still exist, but they delegate to the canonical manager. Their return shapes remain compatibility-preserving.

## Operator Flow

Operators can inspect execution history with:

```powershell
aja exec list
aja exec show <session_id>
aja exec timeline <session_id>
aja exec diff <session_id>
aja exec cleanup
```
