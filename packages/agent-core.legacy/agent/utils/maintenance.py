import time
import logging
from agent.memory.secretary import get_secretary_memory
from agent.config import PROJECT_ROOT
from agent.persistence.tasks import cleanup_old_tasks as cleanup_core_tasks
from agent.persistence.tools import cleanup_old_entries
from agent.decision.feedback import cleanup_old_decisions
from agent.decision.failure_analysis import cleanup_old_failures

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("agent.maintenance")

def run_maintenance():
    """
    Background maintenance service for Agent.
    Cleans up old logs, prunes LanceDB, and verifies system integrity.
    """
    logger.info("Starting Agent Maintenance Service...")
    mem = get_secretary_memory()
    
    while True:
        try:
            logger.info("Executing periodic pruning...")
            # Assistant Memory Pruning
            mem.prune_events(max_rows=1000)
            mem.cleanup_old_tasks(ttl_days=30)
            mem.cleanup_old_approvals(ttl_days=30)
            
            # Core Engine Pruning
            cleanup_core_tasks(ttl_days=30)
            cleanup_old_entries(ttl_days=30)
            cleanup_old_decisions(ttl_days=30)
            cleanup_old_failures(ttl_days=30)
            
            # Additional cleanup tasks could go here
            # (e.g. cleaning .agent/batons/ older than 24h)
            baton_dir = PROJECT_ROOT / ".agent" / "batons"
            if baton_dir.exists():
                count = 0
                now = time.time()
                for f in baton_dir.glob("*.json"):
                    if now - f.stat().st_mtime > 86400: # 24 hours
                        f.unlink()
                        count += 1
                if count > 0:
                    logger.info(f"Cleaned up {count} stale baton files.")
            
            # --- Phase 26: Reflective Learning (Advanced) ---
            try:
                import asyncio
                from agent.autonomy.reflection import run_reflection
                logger.info("Triggering reflective learning loop...")
                asyncio.run(run_reflection())
            except Exception as e:
                logger.error(f"Reflection failed: {e}")

            # --- Phase 5: Handover Hygiene ---
            try:
                from agent.orchestration.handover import HandoverManager
                manager = HandoverManager()
                manager.cleanup_expired()
            except Exception as e:
                logger.error(f"Handover cleanup failed: {e}")

            logger.info("Maintenance cycle complete. Sleeping for 1 hour.")
            time.sleep(3600) # Run every hour
        except KeyboardInterrupt:
            logger.info("Maintenance service stopping...")
            break
        except Exception as e:
            logger.error(f"Maintenance error: {e}")
            time.sleep(60) # Retry after 1 minute if it fails

if __name__ == "__main__":
    run_maintenance()
