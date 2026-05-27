# Changelog

All notable changes to the AJA project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
