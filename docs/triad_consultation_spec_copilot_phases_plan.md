# Triad Architectural Consultation: Phased Execution Plan for GitHub Copilot Decoupling & Isolation

**Date:** 2026-10-19  
**Panel:** Triad Multi-Model Architecture Review (Antigravity Lead + Constellation GLM-5.3 + Constellation Qwen-2.5)  
**Topic:** Reviewing the phased task breakdown, technical specifications, and test contracts for isolating Copilot from AJA Core.

---

## 1. Plan Overview & Objectives

Following the unanimous Triad consensus on **Option 2 (Strict Encapsulation / Extension Isolation with a Hard Deprecation Trigger)**, this implementation plan establishes a zero-tech-debt boundary around the GitHub Copilot integration without disrupting developers who use their existing subscriptions.

### Core Architecture Goals:
1. **Purity of Core:** Zero references to `copilot` in `aja/orchestration/providers/openai_compat.py`, `aja/orchestration/gateway.py`, and `aja/orchestration/context_window.py`.
2. **Dynamic Provider & Model Registration:** Model aliases, context limits, and token management are supplied by the adapter, not hardcoded into core lookup tables.
3. **Packaging Isolation:** Copilot logic encapsulated into an optional extra/plugin (`aja[copilot]` or `aja.providers_extra.copilot`).
4. **Single-Flight Concurrency & Circuit Breaking:** Mutexed token refresh preventing thundering herds during swarm turns, backed by a 3-strike circuit breaker and first-run TOS disclaimer gate.
5. **Pre-Committed Exit Strategy:** Hard deprecation trigger if the un-SLA'd endpoint breaks twice in 6 months or usage drops <5%.

---

## 2. Proposed Phased Breakdown

### Phase 1: Core Decoupling & Model Agnosticism (Immediate / Zero Breaking Changes)
- **Task 1.1:** Purge Copilot Auth & Refresh Leaks from `openai_compat.py`:
  - Remove `_resolve_copilot_key()`, `_refresh_copilot_auth()`, and 401/403 copilot refresh branches.
  - Verify `OpenAICompatAdapter` is 100% provider-agnostic.
- **Task 1.2:** Registry-Driven Model Specs & Context Windows:
  - Refactor `context_window.py` and `model_spec.py` to allow adapters to register their custom context limits and aliases dynamically.
  - Remove hardcoded `copilot` dictionary entries from core modules.
- **Task 1.3:** AST Agnosticism Contract Tests:
  - Add AST and import-linter assertions ensuring `aja/orchestration/providers/openai_compat.py` and `gateway.py` have zero `copilot` occurrences.

### Phase 2: Provider Plugin Architecture & Packaging (`aja[copilot]`)
- **Task 2.1:** Plugin Entry Point Interface (`ChatProvider` / `ProviderAdapter`):
  - Standardize entry-point discovery via `importlib.metadata.entry_points(group="aja.providers")`.
  - Lazy provider loading: a missing or uninstalled provider degrades gracefully with `ProviderUnavailableError`.
- **Task 2.2:** Extract `copilot` into `aja.providers_extra.copilot`:
  - Move `copilot_auth.py` and `copilot_adapter.py` into `aja/providers_extra/copilot/` (or `packages/aja-copilot`).
  - Implement `TokenManager` with single-flight `asyncio.Lock` to eliminate race conditions during swarm execution.
- **Task 2.3:** First-Run TOS Disclaimer & Safety Gate:
  - Interactive / config flag `copilot_tos_acknowledged` required before first invocation of Copilot adapter.
  - Transparent banner alerting user that Copilot connects to an internal VS Code IDE endpoint subject to GitHub TOS.

### Phase 3: Resilience, Observability & Hard Deprecation Gate
- **Task 3.1:** Circuit Breaker & Exponential Backoff:
  - On 3 consecutive 401/403 auth failures, trip circuit breaker for 5 minutes, preventing spamming GitHub's endpoint.
  - Universal log redaction for token exchanges and `Authorization` headers.
- **Task 3.2:** Pre-Committed Deprecation Trigger & Governance:
  - Healthcheck probe script to detect breaking changes in Copilot endpoints.
  - Formal deprecation policy: if 2 breaking endpoint changes occur within 6 months, auto-trigger Option 1 (complete removal) in the next minor release.

---

## 3. Review Questions for the Triad Panel

1. **Sequencing & Granularity:** Does this 3-phase breakdown sequence risk and blast radius properly? Are any tasks too large or missing dependencies?
2. **Packaging Strategy:** Should Copilot be an internal namespace package (`aja.providers_extra.copilot` with optional install `aja[copilot]`) or a completely separate repository/wheel (`aja-copilot`)?
3. **Token Management & Swarm Concurrency:** Does the proposed `TokenManager` with single-flight mutex adequately protect against thundering-herd token refresh during high-concurrency swarm execution?
4. **Backward Compatibility:** How do we guarantee that existing users running `aja --model copilot:gpt-4o` experience zero downtime or migration friction during Phase 1 and Phase 2?

---
