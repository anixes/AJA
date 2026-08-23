"""Google Calendar integration package for AJA.

Public surface:
- auth: is_connected / connect / disconnect / get_service
- events: list_events / create_event
- graph_sync: sync_to_graph / events_between
"""

from aja.calendar import auth, events, graph_sync

__all__ = ["auth", "events", "graph_sync"]
