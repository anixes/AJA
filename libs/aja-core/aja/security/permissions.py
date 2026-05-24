from aja.security.command_guard import command_allowed

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
        return command_allowed(cmd)

class PermissionError(Exception):
    pass

# Default permission set
# We keep only the most critical system-destroying strings here.
# Other dangerous commands like 'rm' are handled by the Risk Level system
# which prompts the user for confirmation instead of a hard block.
default_permissions = Permission(
    allowed_capabilities=["terminal.exec", "agent.coder", "agent.browser"],
    blocked_commands=["shutdown", "mkfs", ":(){:|:&};:", "mv /", "rm /"]
)
