import sys
import os
from pathlib import Path

def main():
    """
    Entry point for the 'aja' console script.
    It routes to the internal main module.
    """
    from aja.main import main as run_cli
    run_cli()

if __name__ == "__main__":
    main()
