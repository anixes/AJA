# Phase 4 Plan: Multi-Agent Swarm (Baton Protocol)

## Objective
Enable Agent to spawn specialized sub-agents and manage persistent tool connections via MCP.

## Wave 1: Native MCP Connectivity
- [ ] **Task 4.1: Implement MCPClient**
  - Create `packages/agent-core/agent/api/mcp_client.py`.
  - Support stdio-based JSON-RPC communication with MCP servers.
- [ ] **Task 4.2: Dynamic Tool Discovery**
  - Implement `list_tools` and `call_tool` mapping for MCP servers.
  - Verify connectivity with a sample MCP server.

## Wave 2: The Baton Protocol (State Handoff)
- [ ] **Task 4.3: Baton Serialization**
  - Implement `BatonManager` in `agent-core`.
  - Support serializing MemoryTree, current task, and tool context to a unique ID.
- [ ] **Task 4.4: Mission Pickup CLI**
  - Add `pickup` command to `agent.main`.
  - Logic to "thaw" a baton and resume the orchestration loop.

## Wave 3: Swarm Coordination
- [ ] **Task 4.5: Native Spawning**
  - Update `UnifiedGateway.spawn_sub_agent` to launch new processes with a baton.
  - Implement heartbeat/status tracking for active sub-agents.
- [ ] **Task 4.6: Delegation Capability**
  - Define a `delegate` tool that triggers a sub-agent spawn.
  - Verify full loop: Main -> Delegate -> Sub-agent -> Result -> Main.

## Verification
- `pytest tests/test_mcp_client.py`
- `agent pickup <id>` manual test.
- Full swarm simulation mission.
