from aja.orchestration.activity_rt import ActivityType
from aja.orchestration.tools.native import NativeToolRegistry


def test_native_registry_dispatch_includes_trusted_scope_metadata():
    registry = NativeToolRegistry()

    activity = registry.dispatch("browser.navigate", {"url": "https://example.com"}, "tr-native")

    assert activity.activity_type == ActivityType.BROWSER
    assert activity.metadata["required_scope"] == "browser.navigate"
    assert activity.metadata["schema_name"] == "browser.navigate"


def test_native_registry_external_mcp_schema_dispatch():
    NativeToolRegistry.clear_external_schemas(prefix="mcp.fake.")
    NativeToolRegistry.register_external_schema({
        "type": "function",
        "function": {
            "name": "mcp.fake.echo",
            "activity_type": "mcp",
            "retry_policy": "safe",
            "required_scope": "mcp.fake.echo",
            "parameters": {"type": "object", "properties": {}},
            "metadata": {"server_id": "fake", "mcp_tool": "echo"},
        },
    })

    activity = NativeToolRegistry().dispatch("mcp.fake.echo", {"text": "hi"}, "tr-mcp")

    assert activity.activity_type == ActivityType.MCP
    assert activity.metadata["server_id"] == "fake"
    assert activity.metadata["mcp_tool"] == "echo"
    assert activity.metadata["required_scope"] == "mcp.fake.echo"

    NativeToolRegistry.clear_external_schemas(prefix="mcp.fake.")
