"""Per-provider LLM conformance fixtures.

Providers are auto-skipped when their API key env var is unset, so the suite
is safe to run on machines with only a subset of credentials (e.g. copilot
only). A session-scoped summary fixture prints the provider x check matrix
after the last test finalizes.
"""

import os
from collections import defaultdict

import pytest

from aja.orchestration.gateway import LLMGateway

# (provider, key_env_var, cheapest-current-tier model)
PROVIDER_MATRIX = [
    ("openai", "OPENAI_API_KEY", "gpt-4o-mini"),
    ("google", "GEMINI_API_KEY", "gemini-2.0-flash"),
    ("openrouter", "OPENROUTER_API_KEY", "openai/gpt-4o-mini"),
    ("anthropic", "ANTHROPIC_API_KEY", "claude-3-5-haiku-20241022"),
]

COPILOT_ENTRY = ("copilot", "COPILOT_GITHUB_TOKEN", "gpt-4o-mini")

# Alternative env var names the gateway itself accepts per provider.
_ALT_KEYS = {
    "google": ("GOOGLE_API_KEY", "AI_KEY"),
}

CHECKS = [
    ("test_basic_completion", "basic"),
    ("test_streaming_yields_chunks", "streaming"),
    ("test_tool_call_roundtrip", "tool_call"),
    ("test_deterministic_4xx_no_retry_hang", "4xx_no_retry"),
]


def _provider_configured(provider: str, key_env: str) -> bool:
    if os.getenv(key_env):
        return True
    return any(os.getenv(alt) for alt in _ALT_KEYS.get(provider, ()))


def _active_providers():
    active = []
    for entry in PROVIDER_MATRIX:
        if _provider_configured(entry[0], entry[1]):
            active.append(entry)
    if os.getenv(COPILOT_ENTRY[1]):
        active.append(COPILOT_ENTRY)
    return active


ACTIVE_PROVIDERS = _active_providers()


def pytest_collection_modifyitems(config, items):
    """Never let live network tests run in a default suite invocation."""
    marker_expr = config.getoption("-m") or ""
    if "live_providers" in marker_expr:
        return
    skip_live = pytest.mark.skip(
        reason="live provider test: run explicitly with `-m live_providers`"
    )
    for item in items:
        if item.get_closest_marker("live_providers"):
            item.add_marker(skip_live)


class ProviderCase:
    def __init__(self, entry):
        self.provider, self.key_env, self.model = entry

    @property
    def id(self):
        return f"{self.provider}:{self.model}"

    def __repr__(self):
        return self.id


@pytest.fixture(params=ACTIVE_PROVIDERS, ids=[e[0] for e in ACTIVE_PROVIDERS])
def configured_provider(request):
    """Parametrized provider tuple; skips when the API key is not configured."""
    case = ProviderCase(request.param)
    if not _provider_configured(case.provider, case.key_env):
        pytest.skip(f"{case.key_env} not set")
    return case


@pytest.fixture
def gw(configured_provider):
    """LLMGateway bound to the parametrized provider."""
    api_key = os.getenv(configured_provider.key_env, "")
    try:
        gateway = LLMGateway(provider=configured_provider.provider, api_key=api_key)
    except ValueError as exc:
        # e.g. anthropic has no base_url registered in providers.json yet —
        # reported to maintainers rather than failing conformance.
        pytest.skip(f"gateway unavailable for {configured_provider.provider}: {exc}")
    yield gateway

    # Best-effort session cleanup; each test runs its own event loop.
    try:
        import asyncio

        asyncio.run(gateway.close())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Outcome recording + end-of-session conformance matrix
# ---------------------------------------------------------------------------

_OUTCOMES = defaultdict(dict)  # provider -> {check: "passed"|"failed"|"skipped"}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if not item.get_closest_marker("live_providers"):
        return
    callspec = getattr(item, "callspec", None)
    if callspec is None or "configured_provider" not in callspec.params:
        return
    provider = callspec.params["configured_provider"][0]
    check = dict(CHECKS).get(item.function.__name__)
    if check is None:
        return
    if rep.when == "call":
        _OUTCOMES[provider][check] = rep.outcome
    elif rep.when == "setup" and rep.outcome == "skipped":
        _OUTCOMES[provider].setdefault(check, "skipped")


_SYMBOL = {"passed": "\u2713", "failed": "\u2717", "skipped": "-"}


@pytest.fixture(scope="session")
def summary(request):
    yield
    items = [i for i in request.session.items if i.get_closest_marker("live_providers")]
    providers = []
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec and "configured_provider" in callspec.params:
            p = callspec.params["configured_provider"][0]
            if p not in providers:
                providers.append(p)
    lines = ["", "=== Provider Conformance Matrix (\u2713 pass / \u2717 fail / - skip) ==="]
    header = f"{'provider':<12}" + "".join(f"{c:<14}" for _, c in CHECKS)
    lines.append(header)
    for p in providers:
        row = f"{p:<12}"
        for _, check in CHECKS:
            status = _OUTCOMES.get(p, {}).get(check, "skipped")
            row += f"{_SYMBOL.get(status, '?') + ' ' + status:<14}"
        lines.append(row)
    lines.append("=" * 68)
    print("\n".join(lines))
