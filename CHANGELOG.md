# Changelog

All notable changes to the AJA project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Phase 9 — LLM Harness Grade-Up
- **Replay-based evaluation framework** (`aja/evals/`): `EvalCase` rubric scoring against journaled missions (unexpected TOOL_FAILED caps at 0.5), regression-gate mode vs stored baselines, `aja eval` CLI.
- **Structured output** (`aja/llm_structured.py`): forced synthetic-tool strategy + JSON-extraction fallback + repair round-trip; planner bracket-slicing replaced; `execute_direct(output_contract=...)` validates final synthesis.
- **Neutral-prompt mode**: `AJA_NEUTRAL_PROMPTS=1` / `swarm_settings.neutral_prompts` swaps the secretary persona for a neutral operator prompt (evals/benchmarks).
- **Provider conformance suite** (`tests/python/live/`, marker `live_providers`): per-provider basic/streaming/tool-call/4xx checks, auto-skipping unconfigured keys. Copilot passes all live.
- **Library-grade loop decoupling**: `run_direct_loop(...)` in `orchestration/direct_loop.py` — stdlib-only module scope, injectable gateway/registry/executor/hooks; subprocess-proven isolation (zero LanceDB/config/bridge imports). `SwarmEngine.execute_direct` remains a thin adapter.

#### Phase 8 — Columnar Batons v2 & Rust Modernization
- **Columnar baton schema v2** (`aja/runtime/baton_state.py`): history as Arrow list columns (`hist_role/hist_content/hist_ts`) + `schema_version` discriminator; `ColumnarBatonState` lazy reader (O(1) len, per-turn decode, streaming iteration, opt-in materialization); parse-free pickup — **18ms cold at 10,000 turns (v1: 35.5ms)**; permanent v1 fallback reader; `BatonCorruptionError` replaces silent `{}`; `AJA_BATON_SCHEMA` rollout flag.
- **Rust modernization** (`packages/aja-native`): pyo3 0.21→0.29 (Bound API), true GIL-free tokenizer via `py.detach`, panic-free error handling (`PyIOError`/`PyValueError` differentiation), typed vendored-blob SHA256 verification at init, tiktoken-rs 0.12, `[profile.release]` tuning, license/publish metadata; dead export removed, mission-format exports deprecated.
- **Contract/benchmark suites**: cross-format matrix (v1↔v2 reads, truncation, fleet-loop-on-v2, cache≡disk) + pickup latency benchmarks at 10/100/1k/10k turns.

#### Phase 7 — Performance Baselines, Keyring Vault & Discord Depth
- **Performance measurement layer**: benchmark suite (`tests/python/benchmarks/`, marker `benchmark`, xdist-safe) covering classify_command latency, embedding warm latency, LanceDB round-trips, journal emit throughput, registry dispatch; mission profiler (`scripts/profile_mission.py`); baseline numbers in `docs/operator/PERFORMANCE.md` (classify ~1.3ms/call, MiniLM warm ~10ms, LanceDB round-trip ~34ms).
- **Copilot token → OS keyring**: resolution order keyring → env → `.env` → gh CLI; dual-write on login (keyring + ACL'd `.env` fallback); `migrate_token_to_keyring()` helper; exception-wrapped for headless environments. `keyring>=25` added to core deps.
- **Discord adapter full Telegram parity** (`discord_adapter.py`, 631 lines): shared approval engine (`gateway/approvals.py::resolve_approval`) with Telegram delegation; persistent approval buttons with per-interaction auth; telemetry pipeline parity (bounded queues, per-channel dispatcher fan-out, LanceDB poller via `to_thread`, lifecycle-managed tasks); vision attachments → data URLs; metrics/health snapshot parity; resilient connect backoff + complete stop cleanup.

#### Phase 6 — Real Web Capabilities & Fleet
- **`search_web` / `fetch_url` tools** (`aja/tools/web.py`): pluggable providers (Serper/Brave/Bing API keys; zero-config DuckDuckGo POST fallback), clean markdown extraction with content-type guards and truncation caps; registered in `NativeToolRegistry` so the WebResearcher persona's tools exist.
- **Browser automation depth**: `browser.extract_markdown`, `browser.wait_for_selector`, `browser.wait_for_network_idle`; structured `BrowserActionError` normalization (timeout/selector/navigation); parameterized timeouts.
- **Fleet deployment story**: full baton loop integration test (capture → signed transmit → HMAC-verified receive → pickup incl. unsigned/tampered rejection) + operator guide (`docs/operator/FLEET.md`).
- **Research flow E2E**: WebResearcher operating pattern (search → select → fetch → synthesize-with-citations) tested through the real registry path.

#### Phase 5 Follow-Ups
- **Skill-runtime CommandGuard**: Every recorded shell step in `SkillExecutor` is re-classified through `classify_command` before execution — `deny` steps abort the run cleanly; `ask` steps deny by default unless explicitly permitted via `allow_ask_steps`.
- **Per-platform gateway authorization**: Unified `aja/gateway/auth.py::is_user_authorized(platform, user_id)` with `DISCORD_ALLOWED_USER_IDS` / `SLACK_ALLOWED_USER_IDS` envs; Discord/Slack intake checks wired through it; Gateway Auth Posture reported by `aja doctor`; schema formalized as `GatewayAuthConfig`.

#### Phase 5 — Full-Stack Audit Remediation
- **Worker registry implementation**: `update_worker`/`delete_worker`/`get_worker`/`seed_default_workers`/`log_worker_execution` implemented (previously called but never defined); `WORKERS_SCHEMA` extended with registry columns + `add_columns` migration; new `aja_worker_executions` table with track-record folding.
- **Telemetry fan-out dispatcher**: per-chat queues fed by a single dispatcher (events previously went to one arbitrary chat); lifecycle-tracked tail tasks; bounded telemetry queue with drop-oldest and approval-exemption.
- **Secret redaction utility** (`aja/utils/redact.py`) applied across gateway, guard, swarm, and planner output paths.
- **Retention**: `cleanup_old_tasks` / `cleanup_old_approvals` / `prune_events` now perform real terminal+stale row deletion.

#### Phase 4 — Security Hardening Pass
- **CommandGuard strict-deny semantics**: Git restricted to a safe subcommand allowlist with `-c` config injection denied; redirects disqualify the known-safe fast path; process-spawning/network cmdlets removed from the PowerShell whitelist; exact-token `rm` matching; fail-closed workspace-boundary checks.
- **Root-deletion detection hardened**: Quoted/POSIX root `Remove-Item` variants (`-Force /`, `C:\*`) deny while ordinary deep paths still route to operator confirmation.
- **Baton transfer security**: Baton codes validated against `^[A-Z0-9]{6}$` (kills path traversal), HMAC-SHA256 authentication via `AJA_BATON_SECRET`, HTTPS enforcement for non-local endpoints, 10 MB payload cap with strict base64 validation, `secrets`-based code generation, and `arrow_ref` containment within the baton directory.
- **Skill compiler injection resistance**: Goal/payload literal embedding via `repr()`, dangerous-construct AST scan (`os.system`, `eval`, `exec`, `__import__`, forbidden modules), validate-before-write ordering.
- **AJAGuard result contract**: `check_and_execute` returns structured status dicts (`executed`/`denied`/`cancelled`/failed`) with injectable gateway and input function; decorative LLM risk-analysis gate removed.
- **Security regression suite**: New tests covering baton traversal/auth, skill injection resistance, guard strict-deny bypasses, quoted-root deletion variants, and fail-closed sandboxing.
- **Per-test timeout ceiling**: pytest-timeout enforced globally (300s) so hung tests cannot stall CI.

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
- **Real ConPTY support on pywinpty 3.x**: import fallback (module `winpty`), adapted `spawn()` signature, non-blocking native reads (blocking reads parked forever post-child-exit), and PTY-failure fallback no longer uses half-constructed transports.
- **pytest-xdist isolation activated**: conftest checked the legacy `_PYTEST_XDIST_WORKER` env name, but modern xdist sets `PYTEST_XDIST_WORKER`. Per-worker `AJA_DATA_DIR`/`AJA_TRACE_DIR` isolation never engaged, so all parallel workers shared one LanceDB directory → native crashes ("node down") → xdist controller INTERNALERROR stall. Fixed by honoring both names; the full suite now passes `-n 8 --dist loadgroup` with **601 passed in <2 min** (previously ~29 min serial and unstable under xdist).
- **PTY/xdist wedge**: Bounded reader reaping in `ExecutionManager._run_session` (`asyncio.wait` timeout=5s); force-close-before-cancel ConPTY cleanup with `io`/`close` locks in `WindowsPTYTransport`; fast-fail timeout marks on PTY-stress tests so a wedged reader can no longer hang the whole xdist run.
- **Copilot token storage hardening**: `.env` ACLs restricted after write (`icacls` on Windows, `chmod 600` on POSIX); the Copilot token is no longer exported into child-process environments by default — opt back in via `AJA_EXPORT_COPILOT_TOKEN=1`.
- **Phase 5 audit fixes**: gateway `datetime` NameError; streaming-fallback `logger` NameError; Google API key moved to header with exception scrubbing; torn-JSONL journal poisoning; non-atomic gateway session persistence (merge_insert + sanitized predicates); cross-process heartbeat race (merge_insert upsert); phantom shard mission projections; O(n²) journal emit; blocking LanceDB calls on the event loop; open platform authorization when bot token set without allowlist; `list_communications` signature mismatch breaking `/communications`; deterministic 4xx provider errors retried 3×; `reflection.py` crashes on None gateway returns; CLI exit-code swallowing in `aja run`; duplicate `_auto_boot_local_worker`; TUI empty-catalog IndexError; llm.py gateway cache-key collision/staleness.
- Fixed critical sandbox jailbreak flaw: `ActivityRuntime._authorize` now evaluates all `ActivityType.SHELL` inputs dynamically with `classify_command` instead of blindly accepting `NativeToolRegistry`'s static generic scopes (e.g., `shell.write`), allowing correct out-of-bounds containment.
- **Fail-closed sandboxing**: `ExecutionManager` now fails the session when workspace sandbox creation fails, instead of silently executing against the live project root.
- **Uniform permission journal contract**: CommandGuard denials in `ActivityRuntime` emit `PERMISSION_DENIED` before `TOOL_FAILED`.
- Latent `NameError`: `bridge.py` logged via an undefined `logger`; duplicate unauthenticated `/tools` route removed; duplicated Telegram `userId` fallback fixed.
- SQLite connection leaks in `BiTemporalEntityGraph` (connections are now always closed); unchanged-upsert returns stored timeline values; falsy-zero `valid_from` bug.
- `ExecutionManager.wait()` raises a descriptive error for unknown sessions and terminal sessions are pruned from the registry (memory leak).
- `AJAGuard` called nonexistent `TokenJuice.compact()` — corrected to `squeeze()`.
- Deflaked `test_conpty_resource_exhaustion` (sleep headroom) and `test_parallel_dag_node_execution` (SwarmEngine class mocked instead of real per-node construction).
- Updated stale gateway test mocks to match the loop-aware reusable `AsyncOpenAI` client contract.
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
