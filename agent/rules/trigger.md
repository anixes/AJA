---
name: trigger
description: Guidelines for when an agent should invoke the AJA CLI tool.
---
# Trigger Rules

Invoke the AJA CLI interface when:
1. Orchestrating complex, long-running multi-agent software engineering or profiling workflows.
2. Running safe dry-run command audits against security constraints using `python -m aja run <objective> --dry-run`.
3. Verifying system configurations, database readiness, and hardware capacities using `python -m aja doctor`.
4. Resuming or picking up a task via serialized Arrow execution batons using `python -m aja pickup <code>`.
5. Visualizing active plan DAGs, tails, and execution status using the curses terminal interface `python -m aja tui`.
