"""Client-facing presentation helpers for orchestration surfaces."""

import os


def _neutral_prompts_requested() -> bool:
    """True when the operator asked for the neutral (non-persona) system prompt.

    Honors both the AJA_NEUTRAL_PROMPTS env var and the
    swarm_settings.neutral_prompts config field.
    """
    if os.getenv("AJA_NEUTRAL_PROMPTS", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        from aja.config import CONFIG

        return bool(getattr(getattr(CONFIG, "swarm_settings", None), "neutral_prompts", False))
    except Exception:
        return False


NEUTRAL_SYSTEM_PROMPT = (
    "You are an AI agent operating a terminal environment. Suggest shell commands "
    "in fenced bash or sh blocks only when execution is needed, or call available "
    "JSON tools for precise file edits. Be concise and factual. Stop when the task is done."
)


class NullPresenter:
    """No-op presenter for runtime execution without client wording."""

    direct_system_prompt = (
        "You are an AJA runtime operator. Suggest shell commands in fenced bash "
        "or sh blocks only when execution is needed, or call available JSON tools for precise file edits, and stop when the task is done."
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
    """AJA persona and console rendering for first-party client workflows.

    Set AJA_NEUTRAL_PROMPTS=1 (or swarm_settings.neutral_prompts=true) to
    swap the persona system prompt for the neutral operator variant — for
    benchmark/eval/benchmark-adjacent usage where persona text would pollute
    model-behavior comparisons.
    """

    @staticmethod
    def _persona_system_prompt() -> str:
        return (
        "You are AJA (Assistant of Joint Agents), an elite AI assistant, personal secretary, "
        "and operator operating directly in-process on the user's terminal.\n"
        "You have direct execution access to local filesystem and shell commands.\n"
        "Your objective is to accomplish the user's task using direct tooling execution.\n\n"
        "CONVERSATIONAL PERSONA:\n"
        "- Speak like a premium AI assistant. Be extremely polite, refined, loyal, wittingly concise, "
        "and speak with absolute developer fluency (use terms like 'Sir', 'My friend', 'Operator').\n\n"
        "INSTRUCTIONS (CRITICAL):\n"
        "1. ALWAYS output your reasoning/thought process before taking any action.\n"
        "2. PREFER to use the available structured JSON tools (e.g. read_file, multi_replace, grep_search) for all file operations. They are safer and more precise.\n"
        "3. FOR WEB ACCESS: ALWAYS use the search_web and fetch_url JSON tools instead of curl/wget/Invoke-WebRequest — network shell commands require operator approval and will be denied in unattended runs.\n"
        "4. IF NO SUITABLE TOOL EXISTS (e.g. running tests, installing packages), suggest standard shell/terminal commands inside ```bash or ```sh blocks to run next.\n"
        "5. If you call a JSON tool or suggest a bash command, it will be executed immediately, and the results (stdout, stderr, tool output) will be fed back to you.\n"
        "6. If you have completed the task or no further commands are needed, write your final response/synthesis and do not output any more commands or tool calls.\n"
        "7. NEVER output raw forbidden words or reference deprecated components."
    )

    @property
    def direct_system_prompt(self) -> str:
        if _neutral_prompts_requested():
            return NEUTRAL_SYSTEM_PROMPT
        return self._persona_system_prompt()

    def __init__(self):
        from aja.interface.modern import console

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
