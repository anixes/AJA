"""Compatibility route groups for the legacy bridge app.

The current FastAPI routes are still registered by ``aja.api.bridge`` to
preserve imports and endpoint paths. These modules declare ownership groups so
future extraction can move handlers without changing public routes.
"""

from . import legacy, memory, runtime, telegram

ROUTE_GROUPS = {
    "runtime": runtime.ROUTE_PATHS,
    "memory": memory.ROUTE_PATHS,
    "telegram": telegram.ROUTE_PATHS,
    "legacy": legacy.ROUTE_PATHS,
}


def attach_route_groups(app):
    """Attach route ownership metadata to the compatibility bridge app."""
    app.state.agentx_route_groups = ROUTE_GROUPS
    return app
