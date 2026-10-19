# GitHub Copilot Decoupling & Isolation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Completely eliminate technical debt and core coupling from GitHub Copilot by isolating it into a standalone provider plugin behind a stable Service Provider Interface (SPI), with dynamic model registration, single-flight token concurrency, circuit breaking, and a pre-committed deprecation trigger.

**Architecture:** AJA Core adopts an extensible Provider SPI (`ProviderAdapter`) where all LLM backends (OpenAI-compat, Anthropic, Google, and Copilot) register dynamically via standard Python entry points or registry calls. Copilot's reverse-engineered authentication (`copilot_auth.py`), dual-protocol adaptation (`/v1/chat/completions` vs `/v1/responses`), and header spoofing are encapsulated in a standalone package (`packages/aja-copilot` / `aja.providers_extra.copilot`) with zero leaked symbols in core modules. A lock-free fast-path + mutexed `TokenManager` prevents thundering-herd token refresh during swarm turns, backed by a 3-strike circuit breaker and an explicit first-run TOS disclaimer gate.

**Tech Stack:** Python 3.11+, `asyncio`, `aiohttp`, `keyring`, `importlib.metadata`, `pytest`, `pytest-asyncio`.

---

## Phased Roadmap Overview

```
Phase 1: Core Decoupling & Dynamic SPI (Purity of Core)
  ├─ Task 1: Clean Provider SPI & Dynamic Model Registry (aja/orchestration/providers/base.py)
  ├─ Task 2: Purge Copilot Auth & Helpers from openai_compat.py
  ├─ Task 3: Migrate Context Window & Model Spec to Registry-Driven Queries
  └─ Task 4: AST Guard & Import Linter Contract Tests

Phase 2: Encapsulation & Concurrency Hardening (Plugin Isolation)
  ├─ Task 5: Monorepo Package Scaffolding (packages/aja-copilot & entry points)
  ├─ Task 6: Single-Flight Two-Tier TokenManager with Fast-Path Validation
  ├─ Task 7: First-Run TOS Disclaimer & Safety Gate
  └─ Task 8: Entry-Point Cold-Start Contract & Verification Tests

Phase 3: Observability, Resilience & Pre-Committed Exit Strategy
  ├─ Task 9: Provider Circuit Breaker (3-Strike 401/403 Lockout)
  ├─ Task 10: Token Redaction Filter & Log Audit
  ├─ Task 11: Automated Endpoint Healthcheck Probe
  └─ Task 12: Pre-Committed Deprecation Trigger & Release Verification
```

---

## Phase 1: Core Decoupling & Dynamic SPI (Purity of Core)

### Task 1: Clean Provider SPI & Dynamic Model Registry

**Files:**
- Modify: `libs/aja-core/aja/orchestration/providers/base.py`
- Modify: `libs/aja-core/aja/orchestration/providers/__init__.py`
- Test: `tests/python/unit/test_provider_spi.py`

**Step 1: Write the failing test**
Create `tests/python/unit/test_provider_spi.py` verifying that:
1. `ProviderAdapter` requires `name`, `capabilities`, `chat`, `stream`, `close`, and `get_model_specs`.
2. A provider can dynamically register its custom model specifications (context window, reasoning flags) without modifying core dictionaries.

```python
import pytest
from aja.orchestration.providers.base import ProviderAdapter, ModelSpec, LLMResponse
from aja.orchestration.providers import register_adapter, get_adapter_class, register_model_spec, resolve_model_spec

def test_provider_spi_contract_and_dynamic_model_specs():
    class CustomAdapter(ProviderAdapter):
        provider_name = "test-provider"
        capabilities = frozenset({"chat", "stream"})

        async def chat(self, model, messages, **kwargs):
            return LLMResponse(content="ok", model=model)

        async def stream(self, model, messages, **kwargs):
            yield "ok"

        async def close(self):
            pass

    register_adapter("custom-p", CustomAdapter)
    register_model_spec(ModelSpec(id="custom-p:fast", context_window=64000, max_tokens=4096))

    spec = resolve_model_spec("custom-p:fast")
    assert spec is not None
    assert spec.context_window == 64000
```

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/python/unit/test_provider_spi.py -v`
Expected: FAIL (`ModelSpec` or `register_model_spec` not found in `base.py`).

**Step 3: Write minimal implementation**
In `libs/aja-core/aja/orchestration/providers/base.py`:
- Add `ModelSpec` dataclass (`id`, `context_window`, `max_output_tokens`, `supports_streaming`, `supports_tools`, `metadata`).
- Update `ProviderAdapter` protocol with abstract properties/methods.
In `libs/aja-core/aja/orchestration/providers/__init__.py`:
- Add `register_model_spec(spec: ModelSpec)` and `resolve_model_spec(model_id: str) -> Optional[ModelSpec]`.

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/python/unit/test_provider_spi.py -v`
Expected: PASS.

**Step 5: Commit**
Run: `git commit -am "feat(providers): establish clean ProviderAdapter SPI and dynamic model registry"`

---

### Task 2: Purge Copilot Auth & Helpers from `openai_compat.py`

**Files:**
- Modify: `libs/aja-core/aja/orchestration/providers/openai_compat.py:90-145`
- Modify: `libs/aja-core/aja/orchestration/providers/__init__.py`
- Test: `tests/python/unit/test_gateway_agnosticism.py`

**Step 1: Write the failing test**
In `tests/python/unit/test_gateway_agnosticism.py`, add `test_openai_compat_contains_zero_copilot_symbols`:

```python
import inspect
from aja.orchestration.providers import openai_compat

def test_openai_compat_contains_zero_copilot_symbols():
    source = inspect.getsource(openai_compat)
    assert "_resolve_copilot_key" not in source
    assert "_refresh_copilot_auth" not in source
    assert "copilot" not in source.lower()
```

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/python/unit/test_gateway_agnosticism.py::test_openai_compat_contains_zero_copilot_symbols -v`
Expected: FAIL (`copilot` occurrences found in `openai_compat.py`).

**Step 3: Write minimal implementation**
In `libs/aja-core/aja/orchestration/providers/openai_compat.py`:
- Delete `_resolve_copilot_key(self, raw_key)` and `_refresh_copilot_auth(self)`.
- In `_resolve_api_key`, remove `if self.provider == "copilot"` branch.
- In `chat()` retry loop, remove `if isinstance(status_code, int) and status_code in (401, 403): if self._refresh_copilot_auth(): continue`.
In `libs/aja-core/aja/orchestration/providers/__init__.py`:
- Remove `"copilot"` from `_register_defaults()` loop (since `CopilotAdapter` is the exclusive handler for `"copilot"`).

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/python/unit/test_gateway_agnosticism.py::test_openai_compat_contains_zero_copilot_symbols -v`
Expected: PASS.

**Step 5: Commit**
Run: `git commit -am "refactor(providers): purge all copilot auth routines and branching from openai_compat.py"`

---

### Task 3: Migrate Context Window & Model Spec to Registry-Driven Queries

**Files:**
- Modify: `libs/aja-core/aja/orchestration/context_window.py`
- Modify: `libs/aja-core/aja/models/model_spec.py`
- Modify: `libs/aja-core/aja/orchestration/providers/copilot_adapter.py`
- Test: `tests/python/unit/gateway/test_model_spec_and_capability_router.py`

**Step 1: Write the failing test**
Verify that context window queries query the dynamic registry before falling back to default heuristic bounds:

```python
from aja.orchestration.context_window import get_context_limit
from aja.orchestration.providers import register_model_spec
from aja.orchestration.providers.base import ModelSpec

def test_context_window_dynamic_registry_resolution():
    register_model_spec(ModelSpec(id="custom:deep-ctx", context_window=256000))
    assert get_context_limit("custom:deep-ctx") == 256000
```

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/python/unit/gateway/test_model_spec_and_capability_router.py -k test_context_window_dynamic_registry_resolution -v`
Expected: FAIL.

**Step 3: Write minimal implementation**
- In `context_window.py`: update `get_context_limit(model: str)` to query `resolve_model_spec(model)` first.
- In `copilot_adapter.py`: have `CopilotAdapter` register its model specs (`copilot:gpt-4o`, `copilot:claude-3.5-sonnet`, etc.) upon module initialization.
- Remove hardcoded `"copilot:"` dictionaries from `context_window.py`.

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/python/unit/gateway/test_model_spec_and_capability_router.py -v`
Expected: PASS.

**Step 5: Commit**
Run: `git commit -am "refactor(orchestration): make context windows registry-driven and remove hardcoded copilot limits"`

---

### Task 4: AST Guard & Import Linter Contract Tests

**Files:**
- Modify: `tests/python/unit/test_gateway_agnosticism.py`

**Step 1: Write failing test**
Add comprehensive AST audit inspecting `gateway.py`, `openai_compat.py`, and `context_window.py` ensuring zero literal string or identifier occurrences of `"copilot"`.

```python
import ast
from pathlib import Path

def test_core_ast_zero_copilot_leakage():
    core_files = [
        Path("libs/aja-core/aja/orchestration/gateway.py"),
        Path("libs/aja-core/aja/orchestration/providers/openai_compat.py"),
        Path("libs/aja-core/aja/orchestration/context_window.py"),
    ]
    for p in core_files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "copilot" not in node.value.lower(), f"Leaked copilot string '{node.value}' in {p}"
```

**Step 2: Run test to verify it fails if any leak remains**
Run: `python -m pytest tests/python/unit/test_gateway_agnosticism.py::test_core_ast_zero_copilot_leakage -v`
Expected: PASS (or fail identifying exact leaked lines).

**Step 3: Fix any detected leaks and commit**
Run: `git commit -am "test(gateway): enforce zero-copilot-leakage AST contract across core files"`

---

## Phase 2: Encapsulation & Concurrency Hardening (Plugin Isolation)

### Task 5: Monorepo Package Scaffolding (`packages/aja-copilot`)

**Files:**
- Create: `packages/aja-copilot/pyproject.toml`
- Create: `packages/aja-copilot/src/aja_copilot/__init__.py`
- Create: `packages/aja-copilot/src/aja_copilot/adapter.py`
- Create: `packages/aja-copilot/src/aja_copilot/auth.py`
- Modify: `libs/aja-core/aja/orchestration/providers/__init__.py`
- Test: `tests/python/unit/test_copilot_plugin_loader.py`

**Step 1: Write the failing test**
Create `tests/python/unit/test_copilot_plugin_loader.py` testing entry-point and fallback discovery:

```python
import pytest
from aja.orchestration.providers import get_adapter_class

def test_copilot_plugin_entry_point_discovery():
    cls = get_adapter_class("copilot")
    assert cls is not None
    assert cls.provider_name == "copilot"
```

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/python/unit/test_copilot_plugin_loader.py -v`
Expected: FAIL.

**Step 3: Write minimal implementation**
1. Scaffold `packages/aja-copilot/pyproject.toml` with:
   ```toml
   [project]
   name = "aja-copilot"
   version = "0.1.0"
   dependencies = ["keyring>=24.0.0", "aiohttp>=3.9.0"]

   [project.entry-points."aja.providers"]
   copilot = "aja_copilot.adapter:CopilotAdapter"
   ```
2. Move Copilot implementation into `packages/aja-copilot/src/aja_copilot/`:
   - `adapter.py` (from `copilot_adapter.py`)
   - `auth.py` (from `copilot_auth.py`)
3. In `libs/aja-core/aja/orchestration/providers/__init__.py`, update `get_adapter_class`:
   ```python
   def get_adapter_class(provider: str) -> Optional[Type[ProviderAdapter]]:
       if provider in _REGISTRY:
           return _REGISTRY[provider]
       # Discover from entry points
       for ep in importlib.metadata.entry_points(group="aja.providers"):
           if ep.name == provider:
               cls = ep.load()
               _REGISTRY[provider] = cls
               return cls
       return None
   ```
4. Install editable plugin in development environment (`pip install -e packages/aja-copilot`).

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/python/unit/test_copilot_plugin_loader.py -v`
Expected: PASS.

**Step 5: Commit**
Run: `git commit -am "feat(plugin): extract aja-copilot into standalone package with entry-point discovery"`

---

### Task 6: Single-Flight Two-Tier TokenManager with Fast-Path Validation

**Files:**
- Create: `packages/aja-copilot/src/aja_copilot/token_manager.py`
- Modify: `packages/aja-copilot/src/aja_copilot/adapter.py`
- Test: `tests/python/unit/test_copilot_token_manager.py`

**Step 1: Write the failing test**
Create `tests/python/unit/test_copilot_token_manager.py`:
Simulate 50 concurrent coroutines calling `get_token()` when token is expired. Assert that exactly ONE network exchange occurs.

```python
import asyncio
import pytest
from aja_copilot.token_manager import TokenManager

@pytest.mark.asyncio
async def test_token_manager_single_flight_under_concurrency():
    call_count = {"exchanges": 0}

    async def fake_exchange():
        call_count["exchanges"] += 1
        await asyncio.sleep(0.05)
        return {"token": "tid-new", "expires_at": asyncio.get_event_loop().time() + 1800}

    mgr = TokenManager(exchange_fn=fake_exchange)
    tokens = await asyncio.gather(*[mgr.get_token() for _ in range(50)])
    assert len(set(tokens)) == 1
    assert tokens[0] == "tid-new"
    assert call_count["exchanges"] == 1
```

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/python/unit/test_copilot_token_manager.py -v`
Expected: FAIL (`TokenManager` not implemented).

**Step 3: Write minimal implementation**
Implement `TokenManager` in `packages/aja-copilot/src/aja_copilot/token_manager.py`:
- Fast path: check in-memory token with >5 min TTL remaining (lock-free).
- Slow path: `async with self._lock:` double-checked locking; single-flight exchange.
- Multi-process support: atomic file lock on token cache file.

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/python/unit/test_copilot_token_manager.py -v`
Expected: PASS.

**Step 5: Commit**
Run: `git commit -am "feat(copilot): implement single-flight TokenManager preventing thundering herd refresh"`

---

### Task 7: First-Run TOS Disclaimer & Safety Gate

**Files:**
- Create: `packages/aja-copilot/src/aja_copilot/tos_gate.py`
- Modify: `packages/aja-copilot/src/aja_copilot/adapter.py`
- Test: `tests/python/unit/test_copilot_tos_gate.py`

**Step 1: Write the failing test**
Assert that calling `CopilotAdapter.chat()` without `copilot_tos_acknowledged=True` raises `TOSAcknowledgmentRequiredError` with clear acceptance instructions.

```python
import pytest
from aja_copilot.adapter import CopilotAdapter
from aja_copilot.tos_gate import TOSAcknowledgmentRequiredError

@pytest.mark.asyncio
async def test_copilot_first_run_requires_tos_acknowledgment(monkeypatch):
    monkeypatch.setenv("AJA_COPILOT_TOS_ACKNOWLEDGED", "0")
    adapter = CopilotAdapter(api_key="k")
    with pytest.raises(TOSAcknowledgmentRequiredError) as exc_info:
        await adapter.chat(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert "GitHub Terms of Service" in str(exc_info.value)
```

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/python/unit/test_copilot_tos_gate.py -v`
Expected: FAIL.

**Step 3: Write minimal implementation**
In `packages/aja-copilot/src/aja_copilot/tos_gate.py`:
- Check `AJA_COPILOT_TOS_ACKNOWLEDGED=1` env var or config flag in `~/.aja/copilot.json`.
- If not acknowledged, raise `TOSAcknowledgmentRequiredError` displaying the transparent disclaimer:
  *"The Copilot provider connects to an internal endpoint intended for IDE extensions. Use is subject to GitHub's Terms of Service and may break without notice. Set AJA_COPILOT_TOS_ACKNOWLEDGED=1 to confirm."*

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/python/unit/test_copilot_tos_gate.py -v`
Expected: PASS.

**Step 5: Commit**
Run: `git commit -am "feat(copilot): add transparent first-run TOS disclaimer gate"`

---

### Task 8: Entry-Point Cold-Start Contract & Verification Tests

**Files:**
- Create: `tests/python/unit/test_copilot_package_contract.py`

**Step 1: Write test verifying complete isolation**
Assert that:
1. When `aja-copilot` is installed, `LLMGateway(provider="copilot")` operates normally.
2. When `aja-copilot` is uninstalled / mocked as missing, `LLMGateway(provider="copilot")` raises `ProviderUnavailableError` directing user to install `aja-copilot`.
3. Running all core unit tests requires zero Copilot mocks.

**Step 2: Run test and verify it passes**
Run: `python -m pytest tests/python/unit/test_copilot_package_contract.py -v`
Expected: PASS.

**Step 3: Commit**
Run: `git commit -am "test(copilot): add entry-point cold-start and uninstalled provider tests"`

---

## Phase 3: Observability, Resilience & Pre-Committed Exit Strategy

### Task 9: Provider Circuit Breaker (3-Strike 401/403 Lockout)

**Files:**
- Modify: `packages/aja-copilot/src/aja_copilot/adapter.py`
- Test: `tests/python/unit/test_copilot_circuit_breaker.py`

**Step 1: Write the failing test**
Assert that after 3 consecutive auth failures (401/403), the circuit breaker trips and immediately fast-fails for 5 minutes without making outbound network requests.

```python
import pytest
from aja_copilot.adapter import CopilotAdapter

@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_3_auth_failures(monkeypatch):
    adapter = CopilotAdapter(api_key="bad-key")
    # Simulate 3 failures
    for _ in range(3):
        res = await adapter.chat(model="gpt-4o", messages=[{"role": "user", "content": "hi"}], retries=0)
        assert res.content == ""
    assert adapter.is_circuit_open() is True
```

**Step 2: Run test to verify it fails**
Run: `python -m pytest tests/python/unit/test_copilot_circuit_breaker.py -v`
Expected: FAIL.

**Step 3: Implement Circuit Breaker**
Add rolling failure window and 5-minute trip timer on persistent auth failures.

**Step 4: Run test to verify it passes**
Run: `python -m pytest tests/python/unit/test_copilot_circuit_breaker.py -v`
Expected: PASS.

**Step 5: Commit**
Run: `git commit -am "feat(copilot): add 3-strike circuit breaker on persistent auth failures"`

---

### Task 10: Token Redaction Filter & Log Audit

**Files:**
- Modify: `packages/aja-copilot/src/aja_copilot/auth.py`
- Test: `tests/python/unit/test_copilot_redaction.py`

**Step 1: Write the failing test**
Assert that token substrings (`ghu_`, `gho_`, session tokens) never appear in logs or error traces.

**Step 2: Implement and verify pass**
Run: `python -m pytest tests/python/unit/test_copilot_redaction.py -v`
Expected: PASS.

**Step 3: Commit**
Run: `git commit -am "sec(copilot): enforce universal log redaction for token exchanges"`

---

### Task 11: Automated Endpoint Healthcheck Probe

**Files:**
- Create: `tools/copilot_healthcheck.py`

**Step 1: Write script**
Implement standalone probe verifying:
- Can exchange token via `copilot_internal/v2/token`.
- Can ping `/v1/chat/completions`.
- Can ping `/v1/responses`.
Returns exit code 0 on healthy, 1 on schema drift / header rejection.

**Step 2: Commit**
Run: `git commit -am "tool(copilot): implement automated healthcheck and schema drift probe"`

---

### Task 12: Pre-Committed Deprecation Trigger & Release Verification

**Files:**
- Create: `docs/governance/copilot_lifecycle_policy.md`

**Step 1: Document Deprecation Trigger Policy**
In `docs/governance/copilot_lifecycle_policy.md`:
1. **Sunset Criteria:** If GitHub alters internal endpoints requiring >1 day emergency patching twice in any 6-month period, or if active usage is <5%, `aja-copilot` is sunset.
2. **Clean Removal Action:** Deleting `packages/aja-copilot` leaves AJA Core 100% operational with zero core modifications required.
3. **Run Full 4-Tier Test Suite:**
   - Tier 1: Core/Gateway unit tests.
   - Tier 2: Orchestration/Runtime unit tests.
   - Tier 3: Interface/Planning unit tests.
   - Tier 4: Integration test suite.

**Step 2: Commit and Tag**
Run: `git commit -am "docs(governance): codify copilot lifecycle policy and pre-committed deprecation trigger"`

---
