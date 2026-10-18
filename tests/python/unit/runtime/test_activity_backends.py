import asyncio

from aja.orchestration.activity_rt import Activity, ActivityRuntime, ActivityType
from aja.security.permissions import PermissionEngine, PermissionPolicy


class FakeMCPManager:
    async def call_tool(self, server_name, tool_name, arguments):
        return {"server": server_name, "tool": tool_name, "arguments": arguments}


class FakeBrowserBackend:
    async def dry_run(self, mission_id, tool, args):
        return {"mission_id": mission_id, "tool": tool, "args": args}

    async def execute(self, mission_id, tool, args):
        return {"executed": True, "mission_id": mission_id, "tool": tool, "args": args}


class FakeDesktopBackend:
    async def dry_run(self, tool, args):
        return {"tool": tool, "args": args}

    async def execute(self, tool, args):
        return {"executed": True, "tool": tool, "args": args}


def allow_all_engine():
    return PermissionEngine(
        PermissionPolicy(
            scopes={
                "mcp.fake.echo": "allow",
                "browser.*": "allow",
                "desktop.interact": "allow",
            }
        )
    )


def test_activity_runtime_mcp_dry_run():
    async def run():
        runtime = ActivityRuntime(dry_run=True, permission_engine=allow_all_engine(), mcp_manager=FakeMCPManager())
        activity = Activity(
            tool="mcp.fake.echo",
            args={"text": "hello"},
            activity_type=ActivityType.MCP,
            trace_id="tr-test",
            metadata={"server_id": "fake", "mcp_tool": "echo", "required_scope": "mcp.fake.echo"},
        )

        return await runtime.run(activity)

    result = asyncio.run(run())

    assert result.success is True
    assert result.data["dry_run"] is True
    assert result.authorized_scope == "mcp.fake.echo"


def test_activity_runtime_browser_dry_run_with_session_id():
    async def run():
        runtime = ActivityRuntime(dry_run=True, permission_engine=allow_all_engine(), browser_backend=FakeBrowserBackend())
        activity = Activity(
            tool="browser.extract_text",
            args={"selector": "body"},
            activity_type=ActivityType.BROWSER,
            trace_id="tr-browser",
            mission_id="mission-browser",
            metadata={"required_scope": "browser.read"},
        )

        return await runtime.run(activity)

    result = asyncio.run(run())

    assert result.success is True
    assert result.data["mission_id"] == "mission-browser"


def test_activity_runtime_desktop_denied_by_policy():
    async def run():
        runtime = ActivityRuntime(
            dry_run=True,
            permission_engine=PermissionEngine(PermissionPolicy(scopes={"desktop.interact": "deny"})),
            desktop_backend=FakeDesktopBackend(),
        )
        activity = Activity(
            tool="desktop.click",
            args={"x": 1, "y": 2},
            activity_type=ActivityType.DESKTOP,
            trace_id="tr-desktop",
        )

        return await runtime.run(activity)

    result = asyncio.run(run())

    assert result.success is False
    assert "Permission denied" in result.error
