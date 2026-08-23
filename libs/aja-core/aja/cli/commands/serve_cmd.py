"""
AJA CLI Command: serve
======================
Single-process composition of gateway + cron scheduler + autonomy loop.
Usage: aja serve
"""

import asyncio
from typing import List

from aja.interface.modern import print_error, print_info


def cmd_serve(args: List[str] = None):
    """Serve AJA as one ambient process: gateway + scheduler + autonomy."""
    from aja.runtime.serve import serve

    print_info("AJA serving: gateway + scheduler + autonomy (Ctrl+C to stop)")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print_error(f"AJA serve failed: {e}")
        raise
