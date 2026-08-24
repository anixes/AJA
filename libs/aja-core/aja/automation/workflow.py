"""
Automation workflow engine.

Users define workflows as Python classes with an async run(ctx) method.
Workflows are discovered from ~/.aja/workflows/*.py and scheduled via
CronScheduler using the class-level `schedule` attribute.
"""

from __future__ import annotations

import importlib.util
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Type

logger = logging.getLogger(__name__)


class WorkflowError(Exception):
    """Raised when a workflow fails during execution."""

    def __init__(self, workflow_name: str, message: str):
        self.workflow_name = workflow_name
        super().__init__(f"[{workflow_name}] {message}")


class Workflow(ABC):
    """Base class for user-defined automation workflows.

    Subclass this and implement run(). Set `name` and optionally `schedule`
    (cron expression) for automatic scheduling.
    """

    name: str = ""
    description: str = ""
    schedule: Optional[str] = None  # cron expression; None = manual only

    @abstractmethod
    async def run(self, ctx: Any) -> Any:
        """Execute the workflow. ctx is a WorkflowContext."""
        ...


def load_workflows(directory: Path) -> List[Workflow]:
    """
    Discovers Workflow subclasses in .py files under directory.
    Returns instantiated instances sorted by name.
    """
    from aja.automation.context import WorkflowContext  # noqa: F401 — ensure module available

    workflows: List[Workflow] = []
    directory = Path(directory)
    if not directory.is_dir():
        return workflows

    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"aja_workflow_{py_file.stem}", str(py_file)
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Workflow)
                    and attr is not Workflow
                    and not inspect_is_abstract(attr)
                ):
                    instance = attr()
                    workflows.append(instance)
                    logger.debug("Discovered workflow: %s", instance.name)
        except Exception as e:
            logger.warning("Failed to load workflow file %s: %s", py_file.name, e)

    return sorted(workflows, key=lambda w: w.name)


def inspect_is_abstract(cls: type) -> bool:
    """Checks if a class has unimplemented abstract methods."""
    import abc

    return getattr(cls, "__abstractmethods__", frozenset()) != frozenset()


async def execute_workflow(
    workflow: Workflow,
    ctx: Any,
) -> Any:
    """
    Runs a single workflow with timing and error handling.

    Raises WorkflowError on failure.
    """
    t0 = time.monotonic()
    try:
        result = await workflow.run(ctx)
        elapsed = time.monotonic() - t0
        logger.info("Workflow '%s' completed in %.2fs", workflow.name, elapsed)
        return result
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error("Workflow '%s' failed after %.2fs: %s", workflow.name, elapsed, e)
        raise WorkflowError(workflow.name, str(e)) from e
