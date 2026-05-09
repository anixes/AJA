class Permission:
    """Capability-level permissions and command sandboxing rules."""
    def __init__(self, allowed_capabilities, blocked_commands):
        self.allowed_capabilities = set(allowed_capabilities)
        self.blocked_commands = blocked_commands

    def allow(self, tool):
        return tool in self.allowed_capabilities

    def validate_command(self, cmd):
        for blocked in self.blocked_commands:
            if blocked in cmd:
                return False
        return True

class PermissionError(Exception):
    pass

# Default strict permission set
default_permissions = Permission(
    allowed_capabilities=["terminal.exec", "agent.coder", "agent.browser"],
    blocked_commands=["rm -rf", "shutdown", "mkfs", ":(){:|:&};:"]
)
