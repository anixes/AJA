# Troubleshooting: Packaging & Import Shadowing Fix

## Issue
Users reported an `AttributeError: module 'agent' has no attribute 'main'` when running the `agent` command. 

## Root Cause
The project had both an `agent.py` script and an `agent/` package directory in the root. 
When the `agent` entry point (installed via `pip install -e .`) tried to `import agent`, the Python interpreter prioritized the `agent/` directory (package) over the `agent.py` file. 
Since the package's `__init__.py` did not expose a `main()` function properly (or was inconsistent), the entry point failed.

Additionally, path resolution using `Path(__file__)` was failing in editable installs because it pointed to the `site-packages` link rather than the physical project root, causing `agent.json` lookup failures.

## Solution
1.  **Project Reorganization**: Moved `agent.py` to `agent/main.py`.
2.  **Unified Entry Point**: Updated `agent/__init__.py` to explicitly export a `main()` function that delegates to `agent.main.main()`.
3.  **Robust Path Resolution**: Replaced `Path(__file__)` with a recursive `find_project_root()` helper in `agent/main.py` and `scripts/tui_shell.py`. This helper looks for `agent.json` or `.git` to accurately identify the project root regardless of how the package is invoked.

## Verification
- Running `agent status` from any directory now correctly identifies the project root and executes the CLI.
- The TUI now correctly loads `agent.json` configuration for online/offline/hybrid modes.
