import pytest
import asyncio
from agentx.autonomy.intent_engine import IntentEngine, Intent

def test_async_intent_engine_lifecycle():
    async def run_test():
        engine = IntentEngine()
        engine.autonomy_enabled = False # disable autonomous action for safety
        
        assert not engine._running
        
        # Start the engine
        engine.start()
        assert engine._running
        assert hasattr(engine, "_task") and engine._task is not None
        
        # Wait a brief moment to ensure task has scheduled
        await asyncio.sleep(0.1)
        
        # Stop the engine
        engine.stop()
        assert not engine._running

    asyncio.run(run_test())

def test_intent_scoring_and_ranking():
    engine = IntentEngine()
    
    intent_safe = Intent("Check system health", "monitor", benefit=0.9, risk=0.05, cost=0.1)
    intent_dangerous = Intent("Delete everything", "delete_files", benefit=0.9, risk=0.9, cost=0.1)
    
    scored_safe = engine.score_intent(intent_safe)
    scored_dangerous = engine.score_intent(intent_dangerous)
    
    assert scored_safe > scored_dangerous
    
    # Safe checks
    assert engine.safe(intent_safe) is True
    assert engine.safe(intent_dangerous) is False
    
    # Ranking
    ranked = engine.rank([intent_safe, intent_dangerous])
    assert intent_safe in ranked
    assert intent_dangerous not in ranked
