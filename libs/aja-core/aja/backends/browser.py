import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class BrowserSession:
    playwright: Any
    browser: Any
    context: Any
    page: Any


class BrowserActionError(RuntimeError):
    """Structured, LLM-actionable browser failure."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class BrowserBackend:
    def __init__(self):
        self._sessions: Dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def execute(self, mission_id: str, tool: str, args: Dict[str, Any]) -> Any:
        action = tool.removeprefix("browser.")
        if action == "close":
            await self.close(mission_id)
            return {"closed": True, "mission_id": mission_id}

        session = await self._get_session(mission_id)
        page = session.page

        try:
            return await self._dispatch(action, page, args)
        except BrowserActionError:
            raise
        except Exception as e:
            # Normalize Playwright's verbose errors into structured, actionable ones.
            name = type(e).__name__
            msg = str(e).splitlines()[0][:500]
            if "Timeout" in name or "timeout" in msg.lower():
                raise BrowserActionError(
                    "timeout",
                    f"{tool} timed out after {args.get('timeout_s', 30)}s. "
                    "Consider wait_for_selector first or check the selector exists.",
                ) from e
            if "wait_for_selector" in msg or "selector" in msg.lower():
                raise BrowserActionError("selector_not_found", f"Selector not found: {msg}") from e
            if "net::" in msg:
                raise BrowserActionError("navigation_failed", f"Network error: {msg}") from e
            raise BrowserActionError("browser_error", f"{name}: {msg}") from e

    async def _dispatch(self, action: str, page: Any, args: Dict[str, Any]) -> Any:
        timeout_ms = int(float(args.get("timeout_s", 30)) * 1000)

        if action == "navigate":
            await page.goto(args["url"], wait_until=args.get("wait_until", "domcontentloaded"), timeout=timeout_ms)
            return {"url": page.url, "title": await page.title()}
        if action == "click":
            await page.click(args["selector"], timeout=timeout_ms)
            return {"clicked": args["selector"]}
        if action == "fill":
            await page.fill(args["selector"], args.get("text", ""), timeout=timeout_ms)
            return {"filled": args["selector"]}
        if action == "screenshot":
            path = args.get("path")
            data = await page.screenshot(path=path, full_page=bool(args.get("full_page", True)), timeout=timeout_ms)
            return {"path": path, "bytes": len(data) if data else 0}
        if action == "extract_text":
            selector = args.get("selector", "body")
            return await page.locator(selector).inner_text(timeout=timeout_ms)
        if action == "extract_markdown":
            from aja.tools.web import _html_to_text

            selector = args.get("selector", "body")
            html = await page.locator(selector).inner_html(timeout=timeout_ms)
            max_chars = int(args.get("max_chars", 8000))
            content = _html_to_text(html)[:max_chars]
            return {"url": page.url, "title": await page.title(), "content": content}
        if action == "wait_for_selector":
            state = args.get("state", "visible")
            await page.wait_for_selector(args["selector"], state=state, timeout=timeout_ms)
            return {"selector": args["selector"], "state": state}
        if action == "wait_for_network_idle":
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            return {"network_idle": True}
        raise ValueError(f"Unknown browser action: {action}")

    async def dry_run(self, mission_id: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "dry_run": True,
            "backend": "browser",
            "mission_id": mission_id,
            "tool": tool,
            "args": args,
        }

    async def close(self, mission_id: str) -> None:
        session = self._sessions.pop(mission_id, None)
        if not session:
            return
        await session.context.close()
        await session.browser.close()
        await session.playwright.stop()

    async def _get_session(self, mission_id: str) -> BrowserSession:
        async with self._lock:
            existing = self._sessions.get(mission_id)
            if existing:
                return existing

            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError("Playwright is not installed. Install aja[browser] to use browser tools.") from exc

            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            session = BrowserSession(playwright=playwright, browser=browser, context=context, page=page)
            self._sessions[mission_id] = session
            return session


_DEFAULT_BROWSER_BACKEND: Optional[BrowserBackend] = None


def get_default_browser_backend() -> BrowserBackend:
    global _DEFAULT_BROWSER_BACKEND
    if _DEFAULT_BROWSER_BACKEND is None:
        _DEFAULT_BROWSER_BACKEND = BrowserBackend()
    return _DEFAULT_BROWSER_BACKEND
