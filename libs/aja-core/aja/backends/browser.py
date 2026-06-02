import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class BrowserSession:
    playwright: Any
    browser: Any
    context: Any
    page: Any


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

        if action == "navigate":
            await page.goto(args["url"], wait_until=args.get("wait_until", "domcontentloaded"))
            return {"url": page.url}
        if action == "click":
            await page.click(args["selector"])
            return {"clicked": args["selector"]}
        if action == "fill":
            await page.fill(args["selector"], args.get("text", ""))
            return {"filled": args["selector"]}
        if action == "screenshot":
            path = args.get("path")
            data = await page.screenshot(path=path, full_page=bool(args.get("full_page", True)))
            return {"path": path, "bytes": len(data) if data else 0}
        if action == "extract_text":
            selector = args.get("selector", "body")
            return await page.locator(selector).inner_text()
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
