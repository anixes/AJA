from .base import Capability, CapabilityResult
from aja.connectors.base import ConnectorRegistry
import logging

logger = logging.getLogger(__name__)

class AppConnectorCapability(Capability):
    """
    Capability to interact with external applications via Agent-style connectors.
    """
    name = "app.connector"
    input_schema = {
        "action": "str (list, sync, call)",
        "connector_id": "str",
        "params": "dict (optional)"
    }

    def execute(self, inputs: dict) -> CapabilityResult:
        action = inputs.get("action")
        connector_id = inputs.get("connector_id")
        params = inputs.get("params", {})

        if action == "list":
            connectors = ConnectorRegistry.list_connectors()
            return CapabilityResult(success=True, output={"connectors": connectors})

        if not connector_id:
            return CapabilityResult(success=False, output={}, error="Missing 'connector_id'")

        connector = ConnectorRegistry.get_connector(connector_id)
        if not connector:
            return CapabilityResult(success=False, output={}, error=f"Connector '{connector_id}' not found")

        try:
            if action == "sync":
                docs = connector.sync()
                # Simplified: return metadata about synced docs
                return CapabilityResult(success=True, output={
                    "synced_count": len(docs),
                    "source": connector_id
                })

            if action == "call":
                tool_name = params.get("tool_name")
                tool_args = params.get("arguments", {})
                if not tool_name:
                    return CapabilityResult(success=False, output={}, error="Missing 'tool_name' for call action")
                
                # Check if it's an MCP tool or native connector tool
                if hasattr(connector, "call_tool"):
                    result = connector.call_tool(tool_name, tool_args)
                else:
                    # Logic to find and call the tool defined in connector.get_tools()
                    result = {"status": "success", "msg": f"Tool {tool_name} executed (simulated)"}
                
                return CapabilityResult(success=True, output={"result": result})

            return CapabilityResult(success=False, output={}, error=f"Unknown action '{action}'")

        except Exception as e:
            logger.error(f"Connector execution failed: {e}")
            return CapabilityResult(success=False, output={}, error=str(e))
