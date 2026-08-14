"""
AJA CLI Command: direct
=======================
Launch persistent Direct Mode interactive developer session.
"""

import asyncio
from aja.interface.modern import console, print_error


def cmd_direct(dry_run: bool = False, model: str = None, resume: bool = False):
    """
    Launch the persistent Direct Mode interactive developer session.
    Maintains rolling conversation memory and leverages prompt caching
    for low-latency multi-turn tool calls on the active workspace.
    """
    from aja.orchestration.direct_session import DirectSession

    session = DirectSession(dry_run=dry_run, model=model, resume=resume)
    try:
        asyncio.run(session.run())
    except KeyboardInterrupt:
        console.print("\n[bold cyan]AJA:[/] Session terminated. Goodbye.")
    except Exception as e:
        print_error(f"Direct Session Error: {e}")
