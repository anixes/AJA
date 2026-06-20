---
name: workflow
description: Standard step-by-step execution workflows for using the AJA CLI.
---
# Workflow Guidelines

Follow these sequential steps when executing tasks with the AJA CLI:
1. **Initialize & Check**: Always run `python -m aja doctor` to verify environment sanity.
2. **Setup (First-Time)**: Run `python -m aja setup` to scaffold data paths and configure default models.
3. **Plan / Simulate**: Run tasks with `--dry-run` first to audit plan transitions and security classifications.
4. **Execute**: Run the live mission or launch the interactive companion console (`python -m aja chat`).
5. **Monitor**: Open a side pane with `python -m aja tui` to monitor live task completion states and telemetry logs.
6. **Review / Apply**: Inspect timeline results with `python -m aja exec list` and apply changes safely via `python -m aja exec apply <session_id>`.
