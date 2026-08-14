"""
AJA CLI Command: projections
=============================
Rebuild derived LanceDB read projections from append-only journals.
"""

from aja.interface.modern import print_error, print_info, print_success


def cmd_rebuild_projections():
    """
    Rebuild derived LanceDB read projections from append-only journals.
    """
    print_info("Rebuilding derived LanceDB projections from append-only journals...")

    try:
        from aja.runtime.mission_journal import rebuild_all_mission_projections

        rebuild_all_mission_projections()
        print_success("Mission read-projections successfully rebuilt.")
    except Exception as e:
        print_error(f"Failed to rebuild mission projections: {e}")

    try:
        from aja.runtime.scheduler_journal import rebuild_scheduler_projections

        rebuild_scheduler_projections()
        print_success("Scheduler read-projections successfully rebuilt.")
    except Exception as e:
        print_error(f"Failed to rebuild scheduler projections: {e}")
