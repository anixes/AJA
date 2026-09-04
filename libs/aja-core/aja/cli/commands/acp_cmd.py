"""
acp_cmd.py - CLI command handler for `aja acp`.
================================================
Launches the Agent Client Protocol (ACP) JSON-RPC 2.0 server over stdio.
Compatible with Zed IDE, JetBrains, and any ACP host.
"""

from __future__ import annotations

import asyncio
import sys
from aja.acp.server import ACPServer


def cmd_acp(args: list[str] | None = None):
    """Launch the ACP stdio server."""
    dry_run = "--dry-run" in (args or [])
    # Log to stderr so stdout remains pure JSON-RPC stream
    sys.stderr.write("[AJA ACP] Starting Agent Client Protocol server on stdio...\n")
    sys.stderr.flush()

    server = ACPServer(dry_run=dry_run)
    try:
        asyncio.run(server.run_stdio())
    except KeyboardInterrupt:
        sys.stderr.write("[AJA ACP] Server stopped by user.\n")
    except Exception as e:
        sys.stderr.write(f"[AJA ACP] Server error: {e}\n")
        sys.exit(1)
