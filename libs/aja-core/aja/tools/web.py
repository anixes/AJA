"""
Web research tools for AJA missions.

Provides ``search_web`` (pluggable providers) and ``fetch_url`` (clean
markdown extraction). Designed to be registered in the NativeToolRegistry so
the WebResearcher specialist's advertised tools actually exist.

Providers (checked in order; first configured wins):
- Serper.dev  — SERPER_API_KEY
- Brave       — BRAVE_API_KEY
- Bing        — BING_API_KEY
- DuckDuckGo HTML — zero-config fallback
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AJA/1.0"
_FETCH_TIMEOUT_S = 20.0
_MAX_FETCH_CHARS = 50_000

# Tags whose entire content is stripped before text extraction.
_STRIP_TAGS = [
    "script", "style", "noscript", "template", "svg", "iframe", "form",
    "nav", "footer", "header", "aside",
]


def _http_get(url: str, timeout: float = _FETCH_TIMEOUT_S) -> str:
    """Minimal GET returning decoded text; raises on HTTP errors."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


# ---------------------------------------------------------------------------
# search_web
# ---------------------------------------------------------------------------


def search_web(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Web search via the first configured provider. Returns [{title, url, snippet}]."""
    import os

    max_results = max(1, min(int(max_results), 10))
    if os.getenv("SERPER_API_KEY"):
        try:
            return _search_serper(query, max_results)
        except Exception as e:
            logger.warning("Serper search failed (%s); falling back.", e)
    if os.getenv("BRAVE_API_KEY"):
        try:
            return _search_brave(query, max_results)
        except Exception as e:
            logger.warning("Brave search failed (%s); falling back.", e)
    if os.getenv("BING_API_KEY"):
        try:
            return _search_bing(query, max_results)
        except Exception as e:
            logger.warning("Bing search failed (%s); falling back.", e)

    return _search_duckduckgo(query, max_results)


def _search_serper(query: str, max_results: int) -> List[Dict[str, Any]]:
    import json
    import os
    import urllib.request

    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps({"q": query, "num": max_results}).encode("utf-8"),
        headers={
            "X-API-KEY": os.environ["SERPER_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
        for r in data.get("organic", [])[:max_results]
    ]


def _search_brave(query: str, max_results: int) -> List[Dict[str, Any]]:
    import json
    import os
    import urllib.request

    req = urllib.request.Request(
        f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={max_results}",
        headers={
            "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
        for r in data.get("web", {}).get("results", [])[:max_results]
    ]


def _search_bing(query: str, max_results: int) -> List[Dict[str, Any]]:
    import json
    import os
    import urllib.request
    import urllib.parse

    req = urllib.request.Request(
        f"https://api.bing.microsoft.com/v7.0/search?q={urllib.parse.quote(query)}&count={max_results}",
        headers={"Ocp-Apim-Subscription-Key": os.environ["BING_API_KEY"]},
    )
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {"title": r.get("name", ""), "url": r.get("url", ""), "snippet": r.get("snippet", "")}
        for r in data.get("webPages", {}).get("value", [])[:max_results]
    ]


def _search_duckduckgo(query: str, max_results: int) -> List[Dict[str, Any]]:
    """
    Zero-config DuckDuckGo HTML scrape (html.duckduckgo.com/html/?q=...).
    Best-effort: layout changes degrade to empty results rather than raising.
    """
    import urllib.parse
    import urllib.request

    results: List[Dict[str, Any]] = []
    try:
        # POST form (a plain GET trips DDG's bot-anomaly page).
        data = urllib.parse.urlencode({"q": query, "b": ""}).encode()
        req = urllib.request.Request(
            "https://html.duckduckgo.com/html/",
            data=data,
            headers={"User-Agent": _USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            html = resp.read().decode(charset, errors="replace")
        # Result anchors look like: <a rel="nofollow" class="result__a" href="...">
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S
        ):
            url = _unwrap_ddg_redirect(m.group(1))
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if url and title:
                results.append({"title": title, "url": url, "snippet": ""})
            if len(results) >= max_results:
                break

        # Snippets live in sibling anchors; best-effort attach by order.
        snippets = [
            re.sub(r"<[^>]+>", "", s.group(1)).strip()
            for s in re.finditer(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
        ]
        for i, snip in enumerate(snippets[: len(results)]):
            results[i]["snippet"] = snip
    except Exception as e:
        logger.warning("DuckDuckGo scrape failed: %s", e)
    return results


def _unwrap_ddg_redirect(href: str) -> str:
    """DDG wraps outbound links in /l/?uddg=<encoded>; unwrap when present."""
    if "//duckduckgo.com/l/" in href or href.startswith("/l/"):
        parsed = urlparse(href if href.startswith("http") else "https://duckduckgo.com" + href)
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def fetch_url(url: str, max_chars: int = 8000) -> Dict[str, Any]:
    """
    Fetches a web page and returns clean markdown-ish content.

    Returns {"url", "title", "content"}. Content is main-text heuristics:
    scripts/styles/nav/chrome stripped, tags removed, entities decoded,
    whitespace collapsed. Non-HTML content types return a short descriptor.
    """
    import urllib.request
    import urllib.error

    max_chars = max(200, min(int(max_chars), _MAX_FETCH_CHARS))
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
            content_type = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            final_url = resp.geturl()
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e

    if content_type == "application/pdf":
        return {"url": final_url, "title": "", "content": f"[PDF document ({len(raw)} bytes)]"}
    if not content_type.startswith("text/") and content_type != "application/xhtml+xml":
        return {"url": final_url, "title": "", "content": f"[Non-HTML content: {content_type}, {len(raw)} bytes]"}

    html = raw.decode(charset, errors="replace")
    title_m = _TITLE_RE.search(html)
    title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
    content = _html_to_text(html)

    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    return {
        "url": final_url,
        "title": title,
        "content": content + ("\n\n[...truncated...]" if truncated else ""),
    }


def _html_to_text(html: str) -> str:
    """Strips non-content chrome and tags; collapses whitespace."""
    text = html
    for tag in _STRIP_TAGS:
        text = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", text, flags=re.S | re.I)
    # Drop comments and the head block entirely.
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<head\b[^>]*>.*?</head>", " ", text, flags=re.S | re.I)
    # Keep link targets visible in markdown-ish form.
    text = re.sub(
        r'<a\s[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2)).strip()} ([{m.group(1)}])",
        text,
        flags=re.S | re.I,
    )
    # Headings become markdown headings.
    for level in range(1, 4):
        text = re.sub(
            rf"<h{level}\b[^>]*>(.*?)</h{level}>",
            lambda m: "\n" + "#" * level + " " + re.sub(r"<[^>]+>", "", m.group(1)).strip() + "\n",
            text,
            flags=re.S | re.I,
        )
    # Block-level breaks become newlines.
    text = re.sub(r"</?(?:p|div|br|li|tr|table|ul|ol|section|article)\b[^>]*>", "\n", text, flags=re.I)
    # Remove every remaining tag.
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities.
    for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                    ("&#39;", "'"), ("&nbsp;", " "), ("&#160;", " "), ("&mdash;", "-")):
        text = text.replace(ent, ch)

    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: List[str] = []
    blank = 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        elif out and blank == 0:
            out.append("")
            blank += 1
    return "\n".join(out).strip()
