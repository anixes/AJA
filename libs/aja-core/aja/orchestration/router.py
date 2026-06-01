from typing import Any

DEVICES = ["phone", "pc", "cloud"]

def route(node: Any) -> str:
    """
    Routes a given execution node to the appropriate device layer.
    """
    # Try to extract node features
    tool = getattr(node, "tool", "").lower()
    description = getattr(node, "description", "").lower()
    
    requires_ui = "ui" in tool or "user" in tool or "ask" in tool or "phone" in description
    system_command = "bash" in tool or "cmd" in tool or "file" in tool or "python" in tool
    long_running = "train" in tool or "crawl" in tool or "heavy" in description
    
    if requires_ui:
        return "phone"
        
    if system_command:
        return "pc"
        
    if long_running:
        return "cloud"
        
    return "pc" # Default to PC execution

def execute_routed_node(node: Any):
    """
    [DEPRECATED] Execution is now handled dynamically by the Native Tool Registry via SwarmEngine.execute_direct.
    """
    print(f"[Router] WARNING: execute_routed_node is deprecated. Use SwarmEngine.execute_direct instead.")
    return False
