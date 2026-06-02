import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from .base import BaseConnector, Document, ConnectorRegistry

logger = logging.getLogger(__name__)


@ConnectorRegistry.register("mcp")
class MCPConnector(BaseConnector):
    """
    The MCP Bridge for Agent.
    This allows Agent to connect to any MCP-compliant server for tool and resource discovery.
    """

    connector_id = "mcp"
    display_name = "Model Context Protocol Bridge"
    auth_type = "config"

    def __init__(self, server_config: Optional[Dict[str, Any]] = None):
        self.config = server_config
        from aja.api.mcp_client import get_default_mcp_manager
        self.manager = get_default_mcp_manager()

    def is_connected(self) -> bool:
        return bool(self.manager.sessions)

    def sync(self, since: Optional[datetime] = None) -> List[Document]:
        # MCP servers usually provide tools/resources rather than a bulk sync
        return []

    def get_tools(self) -> List[Dict[str, Any]]:
        return self.manager.get_registry_schemas()

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Calls a tool on the MCP server via JSON-RPC."""
        return {"error": "Use ActivityRuntime MCP execution for async MCP tool calls."}
