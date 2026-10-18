"""
Unit tests for web research tools (search_web / fetch_url).
All network access is mocked; live smokes are tagged and skipped by default.
"""

import json
from unittest.mock import patch

import pytest

from aja.tools import web


class TestSearchWeb:
    def _mock_urlopen(self, html: str, charset="utf-8"):
        import email.message

        headers = email.message.Message()
        headers["Content-Type"] = f"text/html; charset={charset}"
        ctx = type("R", (), {})()
        ctx.headers = headers
        ctx.get_content_charset = lambda: charset
        ctx.read = lambda: html.encode()
        cm = type("CM", (), {"__enter__": lambda s: ctx, "__exit__": lambda s, *a: False})()
        return cm

    def test_ddg_scrape_parses_results(self):
        html = """
        <div class="result">
        <a rel="nofollow" class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Result <b>One</b></a>
        <a class="result__snippet">First snippet</a>
        <a rel="nofollow" class="result__a" href="https://example.com/b">Result Two</a>
        <a class="result__snippet">Second snippet</a>
        </div>"""
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(html)):
            results = web.search_web("test query", max_results=5)
        assert len(results) == 2
        assert results[0]["url"] == "https://example.com/a"
        assert results[0]["title"] == "Result One"
        assert results[0]["snippet"] == "First snippet"
        assert results[1]["url"] == "https://example.com/b"

    def test_serper_key_routes_to_serper(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "k")
        payload = {"organic": [{"title": "T", "link": "https://x", "snippet": "S"}]}
        with patch("urllib.request.urlopen") as ur:
            ur.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            ur.return_value.__enter__.return_value.status = 200
            results = web.search_web("q")
        assert results == [{"title": "T", "url": "https://x", "snippet": "S"}]

    def test_provider_failure_falls_back_to_ddg(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "k")
        with patch("aja.tools.web._search_serper", side_effect=RuntimeError("boom")):
            with patch("urllib.request.urlopen", return_value=self._mock_urlopen("")):
                results = web.search_web("q")
        assert results == []

    def test_max_results_clamped(self):
        with patch.object(web, "_search_duckduckgo") as ddg:
            web.search_web("q", max_results=99)
            assert ddg.call_args[0][1] == 10


class TestFetchUrl:
    def _serve(self, body, content_type="text/html"):
        import email.message

        headers = email.message.Message()
        headers["Content-Type"] = content_type
        ctx = type("R", (), {})()
        ctx.headers = headers
        ctx.get_content_charset = lambda: "utf-8"
        ctx.geturl = lambda: url
        ctx.read = lambda: (body if isinstance(body, bytes) else body.encode())
        cm = type("CM", (), {"__enter__": lambda s: ctx, "__exit__": lambda s, *a: False})()
        return cm

    def test_fetch_extracts_markdownish_content(self):
        global url
        url = "https://example.com/article"
        html = """<html><head><title>My Article</title><style>x{}</style></head>
        <body><nav>menu junk</nav><h1>Hello</h1><p>World <a href="https://lnk">link text</a></p>
        <script>alert(1)</script></body></html>"""
        with patch("urllib.request.urlopen", return_value=self._serve(html)):
            data = web.fetch_url(url)
        assert data["title"] == "My Article"
        assert "# Hello" in data["content"]
        assert "World" in data["content"]
        assert "alert(1)" not in data["content"]
        assert "menu junk" not in data["content"]

    def test_non_html_returns_descriptor(self):
        global url
        url = "https://example.com/file.zip"
        with patch("urllib.request.urlopen", return_value=self._serve(b"MZ", "application/zip")):
            data = web.fetch_url(url)
        assert "Non-HTML" in data["content"]

    def test_truncation_marker(self):
        global url
        url = "https://example.com/big"
        big = "<html><head></head><body>" + ("word " * 5000) + "</body></html>"
        with patch("urllib.request.urlopen", return_value=self._serve(big)):
            data = web.fetch_url(url, max_chars=1000)
        assert len(data["content"]) < 1200
        assert "truncated" in data["content"]


@pytest.mark.live_web
def test_live_search_smoke():
    """Tagged live test: run with -m live_web."""
    results = web.search_web("python asyncio tutorial", max_results=3)
    assert len(results) >= 1
    assert all(r.get("url") for r in results)


@pytest.mark.live_web
def test_live_fetch_smoke():
    data = web.fetch_url("https://example.com")
    assert "Example Domain" in (data["title"] + data["content"])
