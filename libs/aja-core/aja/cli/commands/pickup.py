"""
AJA CLI Command: pickup
=======================
Resume a mission from a high-performance Arrow Baton.
"""

from aja.interface.modern import console, print_error, print_info, print_success


def cmd_pickup(code: str):
    """
    Resume a mission from a high-performance Arrow Baton.
    """
    if not code:
        print_error("No baton code provided.")
        return

    print_info(f"Picking up mission baton: {code}")
    from aja.orchestration.swarm import SwarmEngine
    from aja.runtime.handover import BatonManager

    mgr = BatonManager()
    state = mgr.pickup(code)

    if not state:
        print_error(
            f"Failed to pick up baton: {code}. It may have expired or does not exist."
        )
        return

    print_success(f"Baton verified. Resuming objective: {state['objective']}")

    engine = SwarmEngine()
    console.print(
        f"[bold cyan]AJA:[/] Resuming mission logic for: [italic]{state['objective']}[/italic]"
    )
