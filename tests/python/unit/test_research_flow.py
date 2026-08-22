"""
B3: Research mission flow E2E — the WebResearcher specialist's standard
operating pattern: search_web -> select -> fetch_url -> synthesize with citations.

Mocked network by default; run variant with live tools is exercised by the
live_web-tagged smokes in test_web_tools.py.
"""

import json
from unittest.mock import patch

from aja.orchestration.tools.native import NativeToolRegistry


def _fake_ddg_html():
    return """
    <a class="result__a" href="https://docs.python.org/3/library/asyncio.html">asyncio — Python docs</a>
    <a class="result__snippet">Event loop documentation.</a>
    <a class="result__a" href="https://realpython.com/asyncio-python/">Understanding asyncio</a>
    <a class="result__snippet">Tutorial article.</a>"""


def _fake_page(url: str):
    return (
        f"<html><head><title>Page {url}</title></head>"
        f"<body><h1>Content</h1><p>About asyncio.</p></body></html>"
    )


def test_research_flow_search_fetch_synthesize():
    registry = NativeToolRegistry()
    registry.register_default_tools()

    # Step 1: search
    with patch("urllib.request.urlopen", return_value=_cm(_fake_ddg_html())):
        results = json.loads(registry.execute("search_web", {"query": "python asyncio", "max_results": 2}))
    assert len(results) == 2 and all(r["url"] for r in results)

    # Step 2: fetch top result (each URL gets its own matching page)
    pages = {}
    def _route(req):
        u = req.get_full_url()
        pages[u] = True
        return _cm(_fake_page(u))
    fetched = []
    for r in results:
        with patch("urllib.request.urlopen", side_effect=lambda req, **kw: _route(req)):
            page = registry.execute("fetch_url", {"url": r["url"], "max_chars": 2000})
        assert "# Content" in page or "About asyncio." in page
        fetched.append({"source": r["url"], "excerpt": page[:120]})

    # Step 3: synthesis payload shape (what the specialist hands to the LLM)
    report_input = {
        "query": "python asyncio",
        "citations": [{"i": i + 1, **f} for i, f in enumerate(fetched)],
    }
    assert len(report_input["citations"]) == 2
    assert all(c["excerpt"] for c in report_input["citations"])


def test_research_tools_dispatch_through_activity_pipeline():
    """The tools dispatch as PYTHON activities with correct scopes."""
    registry = NativeToolRegistry()
    registry.register_default_tools()
    act = registry.dispatch("search_web", {"query": "x"}, trace_id="t1")
    assert act.metadata["required_scope"] == "web.search"
    act2 = registry.dispatch("fetch_url", {"url": "https://x"}, trace_id="t1")
    assert act2.metadata["required_scope"] == "web.read"


# ---------------------------------------------------------------------------
def _cm(body):
    import email.message

    headers = email.message.Message()
    headers["Content-Type"] = "text/html; charset=utf-8"
    data = body.encode() if isinstance(body, str) else body
    ctx = type("R", (), {})()
    ctx.headers = headers
    ctx.get_content_charset = lambda: "utf-8"
    ctx.geturl = lambda: "https://example.com/"
    ctx.read = lambda: data
    return type("CM", (), {"__enter__": lambda s: ctx, "__exit__": lambda s, *a: False})()
