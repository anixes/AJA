"""Runtime-facing command policy helpers for bridge/client surfaces."""

from aja.security.command_guard import classify_command


def analyze_shell_command(command: str):
    return classify_command(command)
