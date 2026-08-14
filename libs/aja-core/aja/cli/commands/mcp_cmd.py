"""
AJA CLI Command: mcp
====================
Inspect and reload MCP server tools.
"""

import asyncio
from typing import List
from aja.interface.modern import print_error, print_success


def cmd_mcp(args: List[str]):
    subcmd = args[0].lower() if args else ""
    if not subcmd or subcmd not in ("reload", "install"):
        print_error("Usage: aja mcp [reload <server_id> | install <server_name>]")
        return

    if len(args) < 2:
        print_error(f"Usage: aja mcp {subcmd} <target>")
        return

    target = args[1]

    if subcmd == "reload":
        from aja.api.mcp_client import get_default_mcp_manager
        from aja.orchestration.tools.native import NativeToolRegistry

        async def _reload():
            manager = get_default_mcp_manager()
            await manager.boot_from_config()
            tools = await manager.reload(target)
            NativeToolRegistry.clear_external_schemas(prefix=f"mcp.{target}.")
            NativeToolRegistry.register_mcp_tools(manager)
            return tools

        try:
            tools = asyncio.run(_reload())
            print_success(f"Reloaded MCP server '{target}' with {len(tools)} tool(s).")
        except Exception as e:
            print_error(f"Failed to reload MCP server '{target}': {e}")

    elif subcmd == "install":
        from aja.mcp import install_mcp_server

        try:
            install_mcp_server(target)
            print_success(
                f"Successfully installed and configured MCP server '{target}' in aja.json."
            )
        except Exception as e:
            print_error(f"Failed to install MCP server '{target}': {e}")
