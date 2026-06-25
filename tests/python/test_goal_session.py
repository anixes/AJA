import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from aja.orchestration.goal_session import GoalSession, GoalSwarmSession, _parse_signal


def test_parse_signal():
    assert _parse_signal("Here is the answer.\n<signal>GOAL_COMPLETE</signal>") == ("complete", "")
    assert _parse_signal("<signal>GOAL_FAILED: Missing dependency</signal>") == ("failed", "Missing dependency")
    assert _parse_signal("I am working on it.") == ("continue", "")
    
    # Test stripping markdown fences
    tricky_reply = "Look at this code:\n```\n<signal>GOAL_COMPLETE</signal>\n```\nWait, not done yet."
    assert _parse_signal(tricky_reply) == ("continue", "")
    
    tricky_reply_2 = "Look at this inline `<signal>GOAL_COMPLETE</signal>`."
    assert _parse_signal(tricky_reply_2) == ("continue", "")


def test_goal_session_complete():
    async def _test():
        session = GoalSession(max_iterations=3)
        
        # Mock DirectSession
        session.session._turn = AsyncMock()
        
        # Define replies that DirectSession will populate in its history
        replies = [
            "Thinking...",
            "<signal>GOAL_COMPLETE</signal>"
        ]
        
        def side_effect_turn(prompt, console, interactive=True):
            reply = replies.pop(0) if replies else "Fallback"
            session.session.session_history.append({"role": "assistant", "content": reply})
        
        session.session._turn.side_effect = side_effect_turn
        
        await session.run("Test objective")
        
        assert session.session._turn.call_count == 2

    asyncio.run(_test())


def test_goal_session_timeout():
    async def _test():
        session = GoalSession(max_iterations=5, timeout_seconds=0.1)
        
        # Mock DirectSession to block indefinitely
        async def blocking_turn(prompt, console, interactive=True):
            await asyncio.sleep(1.0)
        
        session.session._turn = AsyncMock(side_effect=blocking_turn)
        
        # Should not raise exception, but catch timeout internally
        await session.run("Test objective")
        
        assert session.session._turn.call_count == 1

    asyncio.run(_test())


def test_goal_swarm_session_failed():
    async def _test():
        session = GoalSwarmSession(max_iterations=3)
        
        # Mock Planner and Critic
        session.planner_engine.plan_and_execute_batons = AsyncMock()
        session.critic_engine.execute_direct = AsyncMock()
        
        # Critic finds failure on the first attempt
        def critic_side_effect(prompt, session_history, interactive=True):
            session_history.append({"role": "assistant", "content": "<signal>GOAL_FAILED: Cannot continue</signal>"})
            
        session.critic_engine.execute_direct.side_effect = critic_side_effect
        
        await session.run("Test Swarm Objective")
        
        # Planner should run once
        assert session.planner_engine.plan_and_execute_batons.call_count == 1
        # Critic should run once
        assert session.critic_engine.execute_direct.call_count == 1

    asyncio.run(_test())


def test_goal_swarm_session_complete_after_retry():
    async def _test():
        session = GoalSwarmSession(max_iterations=3)
        
        session.planner_engine.plan_and_execute_batons = AsyncMock()
        session.critic_engine.execute_direct = AsyncMock()
        
        replies = [
            "Not done yet",
            "<signal>GOAL_COMPLETE</signal>"
        ]
        
        def critic_side_effect(prompt, session_history, interactive=True):
            reply = replies.pop(0) if replies else "Fallback"
            session_history.append({"role": "assistant", "content": reply})
            
        session.critic_engine.execute_direct.side_effect = critic_side_effect
        
        await session.run("Test Swarm Objective")
        
        assert session.planner_engine.plan_and_execute_batons.call_count == 2
        assert session.critic_engine.execute_direct.call_count == 2

    asyncio.run(_test())
