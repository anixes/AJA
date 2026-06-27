"""
Intent routing regression tests.

Replaces the manual ``benchmark_intent.py`` script with an automated suite
that enforces two contracts:

1. **LLM-fallback contract** — conversational / polite phrasings must NOT be
   trapped by the local router (they must return ``None``).  This was the
   core fix in Phase 3: multi-word NLP heuristics that prevent natural
   language from being incorrectly routed as terminal commands.

2. **Local-routing contract** — direct, unambiguous commands MUST be
   resolved locally without an LLM call, and each local result must be
   returned within 100 ms.

A minimum accuracy threshold is enforced so a single accidentally matched
case does not cause a silent pass.
"""

import time

import pytest

from aja.interface.intent_parser import local_router_fallback

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LOCAL_LATENCY_THRESHOLD_MS = 100  # local routes must be fast
MIN_LLM_FALLBACK_ACCURACY = 1.0  # all conversational cases must fall through
MIN_LOCAL_ACCURACY = 1.0  # all direct cases must be caught locally


def _route(query: str):
    """Call local_router_fallback and return (result, latency_ms)."""
    t0 = time.perf_counter()
    result = local_router_fallback(query)
    latency_ms = (time.perf_counter() - t0) * 1000
    return result, latency_ms


# ---------------------------------------------------------------------------
# 1. LLM-fallback contract
#    Every one of these conversational phrasings must return None so that
#    the LLM can handle them properly.
# ---------------------------------------------------------------------------

LLM_FALLBACK_CASES = [
    # (label, query)
    ("polite ls request", "hey can you list the files in the data science folder inside the d drive please?"),
    ("natural language ls", "i need to see what's inside D:\\data science"),
    ("conversational show files", "could you show me the files in that data science directory?"),
    ("question form ls", "what files are in the data science folder?"),
    ("indirect doctor check", "run a quick doctor check"),
    ("clear screen polite", "just clear the screen"),
    ("conversational gpu", "can you check my gpu usage for me?"),
    ("why did command fail", "hey why did that last command fail?"),
    ("find python files", "can you find me the python files in this project?"),
    ("read config politely", "read that config file for me"),
    ("recent logs polite", "show me the recent logs"),
    ("typo command", "lsi files in data science"),
]


@pytest.mark.parametrize("label,query", LLM_FALLBACK_CASES, ids=[c[0] for c in LLM_FALLBACK_CASES])
def test_conversational_query_falls_through_to_llm(label: str, query: str):
    """Conversational phrasing must NOT be trapped by the local router."""
    result, latency_ms = _route(query)
    assert result is None, (
        f"[{label}] Expected LLM fallback (None) for conversational query:\n"
        f"  query : {query!r}\n"
        f"  got   : {result}\n"
        f"The local router is incorrectly catching natural-language phrasing. "
        f"Check the regex patterns in local_router_fallback()."
    )


def test_llm_fallback_bulk_accuracy():
    """
    Aggregate accuracy gate: ALL conversational cases must return None.
    Fails with a summary table if any case is misrouted.
    """
    misrouted = []
    for label, query in LLM_FALLBACK_CASES:
        result, _ = _route(query)
        if result is not None:
            misrouted.append((label, query, result))

    if misrouted:
        lines = [f"  [{label}] {query!r} => {r}" for label, query, r in misrouted]
        pytest.fail(
            f"{len(misrouted)}/{len(LLM_FALLBACK_CASES)} conversational queries "
            f"were incorrectly routed locally:\n" + "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# 2. Local-routing contract
#    Each of these direct, unambiguous commands must be resolved locally,
#    return the correct command/type, and complete within the latency budget.
# ---------------------------------------------------------------------------

LOCAL_CASES = [
    # (label, query, expected_command_or_type, expected_result_key)
    ("doctor bare", "doctor", "doctor", "command"),
    ("doctor with run", "run doctor", "doctor", "command"),
    ("gpu bare", "gpu", "gpu", "command"),
    ("gpu check", "check gpu", "gpu", "command"),
    ("gpu status", "gpu status", "gpu", "command"),
    ("logs bare", "logs", "logs", "command"),
    ("show logs", "show logs", "logs", "command"),
    ("status bare", "status", "status", "command"),
    ("git status", "git status", "tool_calls", "type"),
    ("ls bare", "ls", "tool_calls", "type"),
    ("list files", "list files", "tool_calls", "type"),
    ("ls in path", "ls in libs/aja-core", "tool_calls", "type"),
    ("exit", "exit", "exit", "command"),
    ("quit", "quit", "exit", "command"),
]


@pytest.mark.parametrize(
    "label,query,expected_value,result_key",
    LOCAL_CASES,
    ids=[c[0] for c in LOCAL_CASES],
)
def test_direct_command_routes_locally(label: str, query: str, expected_value: str, result_key: str):
    """Direct commands must be caught by the local router with correct routing and low latency."""
    result, latency_ms = _route(query)

    assert result is not None, (
        f"[{label}] Expected local route for {query!r} but got LLM fallback (None).\n"
        f"The local router is missing a pattern for this command."
    )
    assert result.get(result_key) == expected_value, (
        f"[{label}] query={query!r}: expected {result_key}={expected_value!r}, "
        f"got {result_key}={result.get(result_key)!r}.\nFull result: {result}"
    )
    assert latency_ms < LOCAL_LATENCY_THRESHOLD_MS, (
        f"[{label}] Local route for {query!r} took {latency_ms:.2f} ms "
        f"(threshold: {LOCAL_LATENCY_THRESHOLD_MS} ms). "
        f"The local router must not perform IO or blocking work."
    )


def test_local_routing_bulk_accuracy():
    """
    Aggregate accuracy gate: ALL direct commands must route locally.
    Fails with a summary table if any case misses.
    """
    missed = []
    for label, query, expected_value, result_key in LOCAL_CASES:
        result, _ = _route(query)
        if result is None or result.get(result_key) != expected_value:
            missed.append((label, query, expected_value, result))

    if missed:
        lines = [f"  [{label}] {query!r} => expected {exp!r}, got {r}" for label, query, exp, r in missed]
        pytest.fail(
            f"{len(missed)}/{len(LOCAL_CASES)} direct commands were not correctly routed locally:\n" + "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# 3. Latency contract
#    All local routes must complete within the budget.
# ---------------------------------------------------------------------------


def test_all_local_routes_meet_latency_budget():
    """No local route may exceed LOCAL_LATENCY_THRESHOLD_MS."""
    slow = []
    for label, query, _, _ in LOCAL_CASES:
        result, latency_ms = _route(query)
        if result is not None and latency_ms >= LOCAL_LATENCY_THRESHOLD_MS:
            slow.append((label, query, latency_ms))

    if slow:
        lines = [f"  [{label}] {query!r}: {ms:.2f} ms" for label, query, ms in slow]
        pytest.fail(f"{len(slow)} local routes exceeded {LOCAL_LATENCY_THRESHOLD_MS} ms:\n" + "\n".join(lines))
