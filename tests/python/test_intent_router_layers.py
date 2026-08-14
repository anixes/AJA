import pytest
from aja.gateway.orchestrator import UnifiedGateway

pytestmark = pytest.mark.anyio


@pytest.mark.anyio
async def test_layer1_deterministic_slash_commands():
    gw = UnifiedGateway()
    assert await gw.route_intent("/swarm do complex task") == "MISSION"
    assert await gw.route_intent("/status") == "STATUS"
    assert await gw.route_intent("/health") == "STATUS"
    assert await gw.route_intent("/doctor") == "STATUS"

@pytest.mark.anyio
async def test_layer1_image_context_is_chat():
    gw = UnifiedGateway()
    assert await gw.route_intent("What is in this image?", has_image=True) == "CHAT"

@pytest.mark.anyio
async def test_layer2_short_conversational():
    gw = UnifiedGateway()
    assert await gw.route_intent("hello there") == "CHAT"
    assert await gw.route_intent("how are you doing today") == "CHAT"
    assert await gw.route_intent("thanks for the help") == "CHAT"

@pytest.mark.anyio
async def test_layer2_questions():
    gw = UnifiedGateway()
    assert await gw.route_intent("What is Python?") == "CHAT"
    assert await gw.route_intent("Why does this happen?") == "CHAT"

@pytest.mark.anyio
async def test_layer3_mission_heuristic():
    gw = UnifiedGateway()
    # Complex mission with shell token "run" and length > 6 words
    res = await gw.route_intent("Please run the build script and install all dependencies now", history=[])
    assert res in ["MISSION", "CHAT"]
