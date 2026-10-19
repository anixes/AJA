# Triad Architectural Consultation Specification
## Remediation of Terminal Assistant REPL & Multi-Provider Model Routing

**Target Subsystems**: `aja.interface.repl`, `aja.llm`, `aja.config`, `aja.orchestration.gateway`  
**Authors**: Antigravity Lead Architect & Triad Architectural Consortium  
**Date**: 2026-10-19  

---

### 1. Problem Statement & Incident Description

When launching the AJA Terminal Assistant via `python -m aja` or `python -m aja repl`, the interactive REPL initializes with:
```text
┌────────────────────────────────────────────────────────┐
│ AJA Terminal Assistant                                 │
│ version dev · provider copilot · model copilot         │
└────── /help for commands · Ctrl+C cancel · Ctrl+D exit ┘
```
Upon the first user turn (`you> wassup?`), the assistant fails with:
```text
[Gateway Facade] Non-retryable provider error (gate 400): Error code: 400 - {
  'error': {
    'code': 'no_route',
    'message': 'Model "copilot" is not available on any provider on this org. Did you mean: z-ai/glm-5, openai/gpt-4o, openai/o1-pro, z-ai/glm-4-7, z-ai/glm-5-1?...',
    'requested_model': 'copilot'
  }
}
⚠️ The model returned an empty response. Check provider configuration with 'aja doctor'.
```
Simultaneously, pandas emit nuisance `UserWarning` regarding `numexpr` and `bottleneck` versions on Anaconda Python environments.

---

### 2. Root Cause Analysis (RCA)

1. **Mismatched Core Construction (`repl.py`)**:
   `TerminalREPL._build_default_core()` invoked `_resolve_production_stack()` with zero arguments, which invoked `get_gateway()` (defaulting to the active model string `"gate:qwen"` from `PROJECT_ROOT / "aja.json"`). Meanwhile, `_build_default_core()` separately resolved `model = AJA_PLANNER_MODEL` (`"copilot"` from `DATA_DIR / "aja.json"`). `ConversationCore` was thus initialized with a Gate AI gateway instance bound to model `"copilot"`. When `gateway.chat(model="copilot", ...)` fired, it targeted Constellation Gate rather than GitHub Copilot API.

2. **Divergent Configuration Paths**:
   - `aja.config.CONFIG_PATH` loaded `DATA_DIR / "aja.json"` (`C:\Users\Asus\AppData\Local\Anixes\AJA\aja.json`), where `models.planner` was `"copilot"`.
   - `aja.llm.resolve_active_model_string()` hardcoded `os.path.join(aja.config.PROJECT_ROOT, "aja.json")` (`d:\AgenticAI\Project1(no-name)\aja.json`), where `active_model` was `"gate:qwen"`.
   - `aja.cli.commands.models.py` only updated `DATA_DIR / "aja.json"` and failed to write `active_model`.
   Different subsystems read contradictory configuration files simultaneously.

3. **Absence of Cross-Provider Routing in `LLMGateway`**:
   `LLMGateway` instances are statically bound to a single `self.provider`. When asked to chat with a model belonging to an alien provider, `LLMGateway.chat` blindly dispatched the request to its own backend URL without checking provider affinity.

4. **Copilot Model Normalization**:
   GitHub Copilot's OpenAI-compatible completions endpoint (`https://api.githubcopilot.com`) expects canonical models such as `gpt-4o` or `gpt-4o-mini`. When `"copilot"` is used as a bare model name, it must deterministically resolve to `gpt-4o`.

---

### 3. Proposed Architectural Remediation

#### Component A: Single Source of Truth for Configuration
- In `aja.config`: Prioritize local workspace config (`PROJECT_ROOT / "aja.json"`) if it exists; fall back to `DATA_DIR / "aja.json"`.
- In `aja.llm`: Replace hardcoded `PROJECT_ROOT / "aja.json"` with `aja.config.CONFIG_PATH`.
- In `aja.cli.commands.models.py`: Update `active_model` and synchronize both `CONFIG_PATH` and `DATA_DIR / "aja.json"`.
- Harmonize workspace and data `aja.json` with `"active_model": "copilot"`.

#### Component B: Synchronized REPL Core Construction
- In `repl.py`: `_build_default_core()` resolves `model = resolve_active_model_string()` *first*, then passes `model` to `_resolve_production_stack(model=model)`, ensuring `get_gateway(model=model)` constructs the matching provider gateway.
- In `collect_health_summary()`: Use `resolve_provider_model()` so the banner displays `provider copilot · model gpt-4o`.
- Suppress pandas UserWarnings at REPL startup.

#### Component C: Cross-Provider Auto-Routing in `LLMGateway.chat`
- In `LLMGateway.chat()`: Inspect the target model's provider. If `target_prov != self.provider`, auto-route to `get_gateway(model).chat(...)`.
- This ensures that multi-provider fallback chains (`["copilot:gpt-4o", "gate:qwen", "google:gemini-2.5-flash"]`) seamlessly transition between distinct API endpoints.

---

### 4. Triad Review Directives & Questions

Please evaluate the proposed remediation across the following architectural dimensions:
1. **Config Determinism**: Does prioritizing `PROJECT_ROOT / "aja.json"` over `DATA_DIR / "aja.json"` prevent developer/operator confusion while preserving wheel/global-install contexts?
2. **Gateway Responsibility**: Is cross-provider delegation within `LLMGateway.chat()` the right seam for multi-provider fallback chains, or should delegation strictly occur at the `direct_loop` caller level?
3. **Copilot Semantics**: When the active model is set to `"copilot"`, is normalizing to `"gpt-4o"` (with `"gpt-4o-mini"` as fallback) optimal for general coding and agentic turns?
4. **Safety & Invariants**: Are there any concurrency or token cache invalidation risks when switching providers mid-session?
