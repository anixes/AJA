import sys
import os
from pathlib import Path

# Ensure stdout/stderr don't crash when printing emojis/non-ASCII on Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def main():
    """
    Entry point for the 'aja' console script.
    It routes to the internal main module.
    """
    from aja.main import main as run_cli
    run_cli()

if __name__ == "__main__":
    main()
