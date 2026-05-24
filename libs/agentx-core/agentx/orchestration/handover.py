import json
import random
import time
from pathlib import Path
from typing import Optional, Dict
from agentx.config import PROJECT_ROOT

HANDOVER_DB = PROJECT_ROOT / ".agentx" / "handover.json"

class ClientHandoverManager:
    """
    Client session handover protocol.
    Generates One-Time Codes (OTC) to bridge terminal sessions to Assistant mobile.
    """
    def __init__(self):
        HANDOVER_DB.parent.mkdir(parents=True, exist_ok=True)
        if not HANDOVER_DB.exists():
            HANDOVER_DB.write_text("{}")

    def generate_otc(self, session_data: Dict) -> str:
        """Generate a 6-digit OTC for the current session."""
        otc = str(random.randint(100000, 999999))
        db = json.loads(HANDOVER_DB.read_text())
        
        # Store OTC with 5-minute expiry
        db[otc] = {
            "expires_at": time.time() + 300,
            "session_data": session_data
        }
        
        HANDOVER_DB.write_text(json.dumps(db, indent=2))
        return otc

    def resolve_otc(self, otc: str) -> Optional[Dict]:
        """Verify OTC and return session data if valid."""
        db = json.loads(HANDOVER_DB.read_text())
        if otc not in db:
            return None
        
        entry = db[otc]
        if time.time() > entry["expires_at"]:
            del db[otc]
            HANDOVER_DB.write_text(json.dumps(db, indent=2))
            return None
            
        # Success - OTC is one-time use
        del db[otc]
        HANDOVER_DB.write_text(json.dumps(db, indent=2))
        return entry["session_data"]

    def cleanup_expired(self):
        """Periodic cleanup of old OTCs."""
        db = json.loads(HANDOVER_DB.read_text())
        now = time.time()
        db = {k: v for k, v in db.items() if v["expires_at"] > now}
        HANDOVER_DB.write_text(json.dumps(db, indent=2))


# Backward-compatible alias for client/UI code that still imports the old name.
# Runtime Arrow batons live in agentx.runtime.handover.BatonManager.
HandoverManager = ClientHandoverManager
