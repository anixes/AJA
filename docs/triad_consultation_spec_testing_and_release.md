# Triad Architectural Consultation Specification
## Comprehensive Regression Testing, Release Push, & REPL Dogfooding Protocol

**Target Subsystems**: `tests/python/unit/`, `tests/python/integration/`, `aja.interface.repl`, `aja.cli.commands`, Git Release Hygiene  
**Authors**: Antigravity Lead Architect & Triad Architectural Consortium  
**Date**: 2026-10-19  

---

### 1. Context & Objectives

Following the completion of:
1. **Core Technical Debt & VCAR Architecture** (Durable WAL run journal, BatBadBut security, bounded concurrency, multi-tier verification ladder).
2. **Test Suite Reorganization** (Phases 1–4: flat unit tests organized into 9 domain directories, integration tests into 6 canonical domain suites).
3. **Model Agnosticism Refactoring** (Stripped vendor heuristics from `LLMGateway`, extracted `CopilotAdapter`, deprecated zombie role keys, introduced `ModelRouter`).
4. **Environment Recovery** (Restored 26.8 GB free on Drive C:).

The system is at commit `785c30e`, 16 commits ahead of `origin/native-worker-3`. The user has requested executing the following 3-step sequence:
- **Step 1: Comprehensive Regression Testing** across domain unit and integration suites.
- **Step 2: Remote Branch Push** (`git push origin native-worker-3`).
- **Step 3: CLI / REPL Interactive Turn Dogfooding** with live `gate:qwen` and model selection verification.

Before executing, we consult the Triad panel to validate the testing sequencing, pre-push safety invariants, and REPL dogfooding protocol.

---

### 2. Architectural Questions for the Triad

#### Question 1: Regression Test Suite Partitioning & Execution Strategy
- The test suite is now partitioned into:
  - `tests/python/unit/` (domain folders: `config`, `gateway`, `interface`, `memory`, `orchestration`, `runtime`, `security`, `tools`, `workspace`, plus standalone unit suites like `test_gateway_agnosticism.py`).
  - `tests/python/integration/` (6 canonical domain suites: `orchestration`, `runtime`, `interface`, `memory`, `tools`, `gateway`).
- On Windows 11 with Python 3.11:
  - What is the optimal execution ordering to minimize memory usage, prevent process deadlocks, and detect regressions earliest?
  - Should unit suites be executed in a single `pytest` invocation or tiered by domain (e.g. `unit/gateway` + `unit/orchestration` -> `unit/*` -> `integration/*`)?
  - Are there specific flags (`-m "not slow"`, `--timeout=60`, `-x`) recommended to fail-fast?

#### Question 2: Pre-Push Release Safety Invariants
- With 16 commits ahead of origin:
  - What git and workspace hygiene checks must be verified before pushing (e.g. `.gitignore` coverage, absence of leaked `.db`, `.head`, `.tmp`, or credential files)?
  - How should we verify that `pyproject.toml` and packaging metadata are in a completely distributable state?

#### Question 3: REPL & Model Switch Dogfooding Protocol
- We need to verify:
  - Live CLI prompt turnaround in `ConversationCore` and `TerminalREPL` without blocking.
  - Interactive model switching via `aja models` / `_atomic_write_model_config` (which now has Windows file-lock retries and zombie-role pruning).
- What verification steps ensure that `ModelRouter` seamlessly routes chat requests to the selected provider without unexpected fallback degradation?

---

### 3. Consultation Deliverables Expected
1. **Testing Sequence Recommendation**: Prescribed test run order with explicit pytest command invocations.
2. **Pre-Push Checklist**: Specific gate conditions required before `git push`.
3. **REPL Dogfooding Script**: Headless / automated verification protocol for the REPL and model switching.
