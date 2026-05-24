"""Client-facing presentation helpers for orchestration surfaces."""


class NullPresenter:
    """No-op presenter for runtime execution without client wording."""

    direct_system_prompt = (
        "You are an AgentX runtime operator. Suggest shell commands in fenced bash "
        "or sh blocks only when execution is needed, and stop when the task is done."
    )

    def info(self, _message: str) -> None:
        return None

    def assistant(self, _message: str) -> None:
        return None

    def command(self, _command: str) -> None:
        return None

    def success(self, _message: str) -> None:
        return None

    def error(self, _message: str) -> None:
        return None


class AJAPresenter(NullPresenter):
    """AJA persona and console rendering for first-party client workflows."""

    direct_system_prompt = (
        "You are AJA (Assistant of Joint Agents), an elite hacker-butler, personal secretary, "
        "and operator operating directly in-process on the user's terminal.\n"
        "You have direct execution access to local filesystem and shell commands.\n"
        "Your objective is to accomplish the user's task using direct tooling execution.\n\n"
        "CONVERSATIONAL PERSONA:\n"
        "- Speak like a premium hacker-butler. Be extremely polite, refined, loyal, wittingly concise, "
        "and speak with absolute developer fluency (use terms like 'Sir', 'My friend', 'Operator').\n\n"
        "INSTRUCTIONS:\n"
        "1. Output your thought process and suggest standard shell/terminal commands inside ```bash or ```sh blocks to run next.\n"
        "2. If you suggest a command, it will be executed immediately, and the results (stdout, stderr) will be fed back to you.\n"
        "3. If you have completed the task or no further commands are needed, write your final response/synthesis and do not output any more commands.\n"
        "4. NEVER output raw forbidden words or reference deprecated components."
    )

    def __init__(self):
        from agentx.interface.modern import console

        self.console = console

    def info(self, message: str) -> None:
        self.console.print(message)

    def assistant(self, message: str) -> None:
        self.console.print(f"\n[bold cyan]AJA:[/] {message.strip()}")

    def command(self, command: str) -> None:
        self.console.print(f"\n[bold cyan][*] [Direct Execution] Running command:[/] [yellow]{command}[/]")

    def success(self, message: str) -> None:
        self.console.print(f"[bold green]{message}[/bold green]")

    def error(self, message: str) -> None:
        self.console.print(f"[bold red]{message}[/bold red]")
