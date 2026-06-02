from typing import Any, Dict, Optional


class DesktopBackend:
    async def execute(self, tool: str, args: Dict[str, Any]) -> Any:
        action = tool.removeprefix("desktop.")
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("pyautogui is not installed. Install aja[desktop] to use desktop tools.") from exc

        if action == "screenshot":
            image = pyautogui.screenshot()
            path = args.get("path")
            if path:
                image.save(path)
            return {"path": path, "size": getattr(image, "size", None)}
        if action == "click":
            pyautogui.click(x=args.get("x"), y=args.get("y"), button=args.get("button", "left"))
            return {"clicked": {"x": args.get("x"), "y": args.get("y")}}
        if action == "type":
            pyautogui.write(args.get("text", ""), interval=float(args.get("interval", 0)))
            return {"typed": len(args.get("text", ""))}
        if action == "hotkey":
            pyautogui.hotkey(*args.get("keys", []))
            return {"hotkey": args.get("keys", [])}
        if action == "move_mouse":
            pyautogui.moveTo(args.get("x"), args.get("y"), duration=float(args.get("duration", 0)))
            return {"moved": {"x": args.get("x"), "y": args.get("y")}}
        raise ValueError(f"Unknown desktop action: {action}")

    async def dry_run(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "dry_run": True,
            "backend": "desktop",
            "tool": tool,
            "args": args,
        }


_DEFAULT_DESKTOP_BACKEND: Optional[DesktopBackend] = None


def get_default_desktop_backend() -> DesktopBackend:
    global _DEFAULT_DESKTOP_BACKEND
    if _DEFAULT_DESKTOP_BACKEND is None:
        _DEFAULT_DESKTOP_BACKEND = DesktopBackend()
    return _DEFAULT_DESKTOP_BACKEND
