# Triad Architectural Consultation Specification
## Pure Model Agnosticism & Legacy Role Deprecation Cleanup

**Target Subsystems**: `aja.orchestration.gateway`, `aja.orchestration.providers`, `aja.config`, `aja.llm`, `aja.orchestration.roles`  
**Authors**: Antigravity Lead Architect & Triad Architectural Consortium  
**Date**: 2026-10-19  

---

### 1. Architectural Philosophy & Context

A foundational architectural pillar of AJA is **strict model and provider agnosticism**:
1. **Uniform Currency**: The runtime (autonomous loop, direct loop, tool executor, memory, interfaces) speaks exclusively in standard prompt messages, tool definitions, and normalized `LLMResponse` objects.
2. **Zero Vendor Special-Casing in Core Transport**: Core routing and gateway layers (`LLMGateway`, `ModelRouter`) must be dumb, reliable transport facilities. They must **never** inspect model strings to branch on vendor names (`"claude"`, `"gpt-4o"`, `"gpt-5-mini"`).
3. **Encapsulated Provider Adapters**: Provider-specific wire formats, authentication token exchanges, and proprietary endpoints belong strictly inside modular `ProviderAdapter` classes under `aja.orchestration.providers.*`.
4. **Single-Harness Modernization**: The runtime was upgraded from the legacy 2025 tri-role ping-pong loop (`models.planner` -> `models.worker` -> `models.critic`) to a unified single-model autonomous harness driven by `active_model` with automated AST/test verification ladders (OpenCode 2 / Harness 2 pattern).

---

### 2. Current Architectural Inconsistencies & Technical Debt

#### A. Vendor Pollution & Wire Protocol Leakage in `LLMGateway` (`gateway.py`)
In `libs/aja-core/aja/orchestration/gateway.py`:
- Lines 354–381 branch on `if self.provider == "copilot":` and inspect model names:
  ```python
  if (m_lower.startswith("gpt-") and not m_lower.startswith("gpt-5-mini") and m_lower not in ("gpt-4o-mini", "gpt-4o", "gpt-4")):
      use_responses = True
  if "claude" in m_lower:
      use_responses = True
  ```
- Lines 435–540 contain 105 lines of raw `aiohttp` code communicating directly with GitHub Copilot's `/v1/responses` preview endpoint, manually building JSON payloads, handling Copilot-specific token invalidation, and parsing custom response formats.
- **Violation**: This completely violates the `ProviderAdapter` design documented in `aja/orchestration/providers/base.py`, which states:
  > *"Every LLM provider implements ProviderAdapter, translating its native wire format to/from the common LLMResponse currency. The gateway resolves the correct adapter from the registry — zero provider-specific branches there."*

#### B. Lingering Zombie Role Keys (`planner`, `worker`, `critic`)
Even though the multi-model role split was superseded:
- `C:\Users\Asus\AppData\Local\Anixes\AJA\aja.json` still persists:
  ```json
  "models": {
      "planner": "copilot",
      "worker": "llama_cpp:LFM2.5-VL-1.6B-Q4_K_M.gguf",
      "critic": "copilot"
  }
  ```
- `libs/aja-core/aja/config.py` still defines fallbacks:
  - `AJA_PLANNER_MODEL = os.getenv("AJA_PLANNER_MODEL", _get_default_model("planner", "google:gemini-2.0-flash"))`
  - `AJA_WORKER_MODEL = os.getenv("AJA_WORKER_MODEL", _get_default_model("worker", "google:gemini-2.0-flash"))`
  - `AJA_CRITIC_MODEL = os.getenv("AJA_CRITIC_MODEL", _get_default_model("critic", AJA_PLANNER_MODEL))`
- `libs/aja-core/aja/llm.py` still preserves fallback priorities 4 and 5 reading `models.worker` and `models.planner` when `active_model` is missing.

#### C. Unused Role Artifacts (`roles.py`)
- `libs/aja-core/aja/orchestration/roles.py` defines `RoleConfig` and `HeterogeneousOrchestrator(Planner, Worker, Verifier)`.
- It is largely decoupled from the primary runtime (`direct_loop.py` / `autonomous_loop.py`), creating ambiguity about how tasks are dispatched.

---

### 3. Proposed Remediation Plan

#### Step 1: Encapsulate Copilot Protocol into `CopilotAdapter`
- Create `libs/aja-core/aja/orchestration/providers/copilot_adapter.py` (or extend `OpenAICompatAdapter` via a dedicated subclass `CopilotAdapter`).
- Move all Copilot-specific token refreshes, `/v1/responses` vs `/v1/chat/completions` payload translation, and model name normalization inside `CopilotAdapter`.
- Strip lines 354–381 and lines 435–540 completely out of `LLMGateway.chat()`.
- Register `register_adapter("copilot", CopilotAdapter)` in `aja.orchestration.providers`.
- Result: `LLMGateway` has zero model-name checks and zero provider branches.

#### Step 2: Purge Zombie Role Fallbacks
- Deprecate and remove `models.planner`, `models.worker`, and `models.critic` in `config_schema.py`.
- Update `config.py` to standardize on `AJA_ACTIVE_MODEL` (fallback: `DEFAULT_MODEL_CHAIN[0]` or `gate:qwen`).
- Clean user machine global config (`~/.local/Anixes/AJA/aja.json`) and workspace config to remove zombie keys.
- Keep a 1-release backwards-compatibility warning shim in `llm.py` for legacy configs.

#### Step 3: Clarify `roles.py` vs Modern Harness
- Mark `HeterogeneousOrchestrator` in `roles.py` as an optional experimental sub-orchestrator or deprecate it if completely unused by active CLI/Telegram flows.

---

### 4. Directives Requested from Triad Panel

1. **Protocol Extraction**: Do you endorse extracting Copilot's `/v1/responses` logic out of `gateway.py` into `aja.orchestration.providers.copilot_adapter.CopilotAdapter`? Are there edge cases in token invalidation / retry during streaming or chat that must be preserved?
2. **Configuration Cleanliness**: What is the safest migration strategy for removing `planner`/`worker`/`critic` from `aja.json` and `config.py` without breaking external CLI scripts or test suites that still read `AJA_PLANNER_MODEL`?
3. **Pure Model Agnosticism Guarantee**: Does the proposed architecture guarantee that ANY provider conforming to `ProviderAdapter` (e.g. OpenAI, Gate, Constellation, Ollama, Llama.cpp, Anthropic, Gemini) can be swapped seamlessly via `active_model` with ZERO core codebase changes?
