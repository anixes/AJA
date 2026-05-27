# AJA Operator Manual

AJA is an enterprise-grade agentic orchestration engine designed to manage autonomous multi-agent missions. This manual outlines how to operate, monitor, and maintain an AJA deployment.

## Architecture & State

AJA acts as a local-first event-sourced durable execution runtime. State transitions (Batons) are serialized via an Apache Arrow IPC engine and cached in RAM for zero-copy handovers, falling back to disk durability in the `AJA_DATA_DIR/batons` directory.

### Key Components:
1. **Core Runtime (`libs/aja-core`)**: Python-based orchestrator, CLI, diagnostics.
2. **Native Extension (`packages/aja-native`)**: PyO3 Rust extension for high-performance operations and Arrow IPC.
3. **Memory Stack**: LanceDB vector database.

## Operating Commands

### Initialization and Diagnostics
Before starting the system, verify operational readiness:

```bash
aja doctor
```
This command checks the environment, configuration schema, native PyO3 engine, LanceDB tables, memory/disk space, and API tokens.

To scaffold a new workspace or regenerate standard layouts:
```bash
aja setup
```

### Running Executions
Run a swarm mission or plan:

```bash
aja run "Perform project analysis"
```

To simulate a plan safely without executing side-effects or mutating local files:
```bash
aja run "Perform project analysis" --dry-run
```

### Telemetry & Observability
AJA includes a trace-aware telemetry context manager. Operations are tracked via an active `trace_id`.

- **Logs**: Logs are written to `AJA_DATA_DIR/logs/`. You can tail them locally or ingest them into an observability platform (Datadog, Splunk).
- **TUI Dashboard**: AJA includes a curses-based live HTN dashboard.
  ```bash
  aja tui
  ```

## Maintenance

### Cron Scheduler
AJA features a persisted LanceDB Cron Scheduler for background tasks.
- Ensure the `aja` background loop process is supervised (e.g., systemd, Docker).
- Timeouts enforce a strict 3-minute limit on swarm execution turns to prevent runaway resource exhaustion.

### Data Management
- **LanceDB**: Vector databases are located at `AJA_DATA_DIR/lancedb`. You may back up this directory periodically.
- **Batons**: Stale handovers are stored in `AJA_DATA_DIR/batons`. The runtime auto-clears short-lived batons, but occasional monitoring of this folder is recommended if executions unexpectedly crash.
