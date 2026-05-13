import sys
import os
from pathlib import Path

def main():
    """
    Entry point for the 'agentx' console script.
    It routes to the internal main module.
    """
    from agentx.main import main as run_cli
    run_cli()

if __name__ == "__main__":
    main()
