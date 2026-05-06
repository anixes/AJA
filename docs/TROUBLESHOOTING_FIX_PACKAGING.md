# Troubleshooting: Packaging & Import Shadowing Fix

## Issue
Users reported an `AttributeError: module 'agentx' has no attribute 'main'` when running the `agentx` command. 

## Root Cause
The project had both an `agentx.py` script and an `agentx/` package directory in the root. 
When the `agentx` entry point (installed via `pip install -e .`) tried to `import agentx`, the Python interpreter prioritized the `agentx/` directory (package) over the `agentx.py` file. 
Since the package's `__init__.py` did not expose a `main()` function properly (or was inconsistent), the entry point failed.

Additionally, path resolution using `Path(__file__)` was failing in editable installs because it pointed to the `site-packages` link rather than the physical project root, causing `agentx.json` lookup failures.

## Solution
1.  **Project Reorganization**: Moved `agentx.py` to `agentx/main.py`.
2.  **Unified Entry Point**: Updated `agentx/__init__.py` to explicitly export a `main()` function that delegates to `agentx.main.main()`.
3.  **Robust Path Resolution**: Replaced `Path(__file__)` with a recursive `find_project_root()` helper in `agentx/main.py` and `scripts/tui_shell.py`. This helper looks for `agentx.json` or `.git` to accurately identify the project root regardless of how the package is invoked.

## Verification
- Running `agentx status` from any directory now correctly identifies the project root and executes the CLI.
- The TUI now correctly loads `agentx.json` configuration for online/offline/hybrid modes.
