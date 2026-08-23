"""
AJA CLI Commands Package
========================
Individual subcommand implementations.
"""

# Eval command is also registered programmatically for in-process dispatch.
from aja.cli.commands.eval_cmd import cmd_eval  # noqa: E402,F401

try:  # best-effort: keep the singleton registry aware of `eval`
    from aja.cli.registry import registry

    registry.register("eval", cmd_eval)
except Exception:  # pragma: no cover - import-order safety
    pass
