# Changelog

All notable changes to the AJA project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Phase 2 — Native Agentic Engine
- **Manager vs Worker architecture**: Replaced legacy bash-string prompting with a structured `SwarmEngine.execute_direct` FSM loop backed by `NativeToolRegistry` and strict JSON-schema tool calling.
- **`/goal` command**: Background single-agent relentless execution loop (`GoalSession`) that audits its own work each iteration until `GOAL_COMPLETE` or `GOAL_FAILED` is signalled.
- **`/schedule` command**: Persistent cron job registry (`SchedulerJournal`) storing jobs in LanceDB, surviving restarts, with pause/resume and configurable timeout shields.
- **Planner / Worker / Critic model roles**: Separate `AJA_PLANNER_MODEL`, `AJA_WORKER_MODEL`, and `AJA_CRITIC_MODEL` environment variables for independent model wiring per role.
- **Zero-copy baton IPC RAM cache**: In-memory Arrow baton buffer (`_IN_MEMORY_BATONS`) enabling sub-millisecond zero-copy handovers with disk-fallback durability.
- **Diversity plan generator**: Dynamic temperature scaling (`temp = min(1.0, 0.3 + attempts × 0.15)`) and prior-plan injection to prevent duplicate plan regeneration loops.
- **Lazy embedding model loading**: `SentenceTransformer` deferred to first `embed()` call, speeding up cold-start CLI response times.
- **Auto-Proceed Local Sandbox Bypasses**: Added `auto_proceed_local` flag to `SwarmSettings`. When active and in `sandbox_mode="local"`, automatic execution approval gracefully skips manual TTY prompts without timing out during synchronous FSM runs or direct asynchronous tool commands.

#### Phase 3 — Production-Grade Activity Runtime
- **Full MCP (Model Context Protocol) client integration**: Real JSON-RPC 2.0 over stdio transports, managed by `MCPClientManager`. MCP tools are journaled, replay-safe, and subject to permission policies.
- **Browser automation backend**: Playwright async browser sessions persisted across a mission (navigate, click, type, extract, screenshot).
- **Desktop automation backend**: `pyautogui` + `pygetwindow` cross-platform GUI interaction backed by structured activity journaling.
- **Structured tool permission system**: Hierarchical declarative scopes (`shell.read`, `shell.exec`, `browser.interact`, `fs.read.global`, etc.) enforced by `PermissionEngine` with `PERMISSION_GRANTED` / `PERMISSION_DENIED` journal events.
- **Parallel activity scheduler**: `ParallelActivityScheduler` executes non-dependent activities concurrently. Python and MCP tools run fully parallel; shell commands serialized; browser/desktop locked per-session to prevent UI collisions. Introduces `ActivityBatchResult` with partial-failure semantics.
- **NLP fallback heuristics**: Multi-word path detection in `local_router_fallback()` ensures conversational phrasings (e.g. `"read that config file for me"`) fall through to the LLM instead of being trapped by terminal regexes.
- **`allow_out_of_bounds_paths` config flag**: Explicit opt-in in `SwarmSettings` for broad filesystem exploration; defaults to `false` (secure).

### Changed
- Removed legacy bash-prompting, `subprocess.run(shell=True)` fast-paths, and all raw string-parsing execution paths. All execution now flows through the agentic FSM loop and `ActivityRuntime`.
- Removed `router.py` (was `aja.orchestration.router`). Execution is now handled entirely by `SwarmEngine.execute_direct` via `NativeToolRegistry`.
- Removed `apps/cli-ts` TypeScript CLI territory from `aja.json` and `cmd_setup`. The `run_file_guardian_check` security hook is now implemented as a pure Python function delegating to `classify_command`.
- `GoalSwarmSession.critic_engine` now uses `AJA_CRITIC_MODEL` (defaults to planner model) instead of `AJA_WORKER_MODEL`, restoring proper adversarial separation between worker and critic roles.
- `providers.json` consolidated to a single canonical root file; `copilot` provider added; Google base URL updated to the OpenAI-compatible `/v1beta/openai` endpoint.

### Fixed
- Fixed critical sandbox jailbreak flaw: `ActivityRuntime._authorize` now evaluates all `ActivityType.SHELL` inputs dynamically with `classify_command` instead of blindly accepting `NativeToolRegistry`'s static generic scopes (e.g., `shell.write`), allowing correct out-of-bounds containment.
- `allow_out_of_bounds_paths` reset to `false` in `aja.json` (was mistakenly left `true`, bypassing the `PermissionEngine` path-boundary check).
- `pylance` removed from `[project.dependencies]` in `pyproject.toml` (it is a VS Code extension, not a PyPI runtime package).
- Windows `asyncio` event loop policy now explicitly set to `WindowsProactorEventLoopPolicy` in `conftest.py` so async subprocess tests work correctly on Windows.
- LanceDB invariant checker (`tests/python/invariants.py`) fully rewritten — all 17 production tables now have active structural, status-enum, datetime, JSON-blob, and uniqueness checks.

### Planned (Roadmap)

The following features are tracked for future releases. Each has a designated owner module
so integration points are clear from the start.

#### Snapshotting (`aja.runtime.snapshot`)
Periodic compaction of append-only mission journals into binary state snapshots,
reducing cold-start replay time for long-running research daemons. Snapshots will
be stored alongside the journal shards and loaded preferentially by
`VersionedEventRehydrator` when available.
- Owner module: `libs/aja-core/aja/runtime/snapshot.py`
- Blocked by: finalising the `VersionedEventRehydrator` event-schema migration.

#### Replay Certification (`aja.runtime.replay_cert`)
Formalized compliance tooling to verify that older JSONL journals can be cleanly
rehydrated by the current `VersionedEventRehydrator`. Intended as a CI gate that
runs against a golden set of archived journals on every schema-touching PR.
- Owner module: `libs/aja-core/aja/runtime/replay_cert.py`
- Depends on: Snapshotting milestone above.

#### Deterministic Concurrency (`aja.orchestration.scheduler`)
Strictly ordered event interleaving for multi-threaded durable activities —
guaranteeing that concurrent `ParallelActivityScheduler` runs produce identical
journals when given identical inputs. Required before parallel activities can be
marked replay-safe.
- Owner module: `libs/aja-core/aja/orchestration/scheduler.py` (extend existing)
- Blocked by: Replay Certification milestone above.

#### Network Egress Filtering (`aja.security.egress`)
Granular per-scope network egress policy enforced at the `ActivityRuntime` level,
preventing LLM-directed exfiltration of secrets over outbound HTTP/S. Policy will
follow the same declarative scope syntax as `PermissionPolicy`.
- Owner module: `libs/aja-core/aja/security/egress.py`

#### Copy-on-Write Sandbox Overlays (`aja.runtime.sandbox`)
CoW filesystem overlay support (OverlayFS on Linux, shadow-copy on Windows) so
that destructive shell activities run inside an isolated layer that can be
committed or discarded atomically.
- Owner module: `libs/aja-core/aja/runtime/sandbox.py` (extend existing)

## [0.1.0] - 2026-05-27

### Added
- AJA Core architecture.
- Pydantic validated configuration schema (`aja.json`).
- Platformdirs standard for durable execution storage (`AJA_DATA_DIR`).
- PyO3 native Rust extension (`aja-native`) for Arrow IPC serialization.
- LanceDB vector database for semantic memory and state retention.
- Zero-copy baton memory cache for sub-millisecond execution handovers.
- Curses-based TUI and real-time KanBan dashboards.
- Multi-interface chat loops (Discord, Slack, local CLI).
- Standalone command-line tools for setup, health-checks (`aja doctor`), and telemetry.
- Release automation and CI matrices for macOS, Windows, and Linux.
- Dockerfile and multi-stage container deployment targets.

### Changed
- Migrated legacy `.aja` data directory usage to system standard application directories.
- Switched default install mechanisms from raw git clones to `pip` distributions and wheels.

### Security
- Fixed `.env` credential exposures.
- Purged stale credentials from history blocks.
