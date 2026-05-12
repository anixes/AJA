# MCP Tool Integration

The Model Context Protocol (MCP) is the standard AgentX uses to extend its capabilities via external servers and plugins.

## 🔌 Integration Architecture

AgentX acts as an **MCP Client** that can orchestrate multiple local and remote **MCP Servers**.

### Supported Transports
1. **Stdio**: Spawns a local process (e.g., `npx @agentx/server-memory`).
2. **SSE/HTTP/WS**: Connects to remote servers via standard web protocols.
3. **SDK**: In-process transport for direct code integration.

---

## 🏗️ Configuration & Discovery

The system merges MCP configurations from several prioritized sources:

| Priority | Source | Description |
| :--- | :--- | :--- |
| **1 (Highest)** | **Enterprise** | `managed-mcp.json` - Hard-locked by corporate policy. |
| **2** | **Manual User** | Global user config added via `agentx mcp add`. |
| **3** | **Project** | `.mcp.json` found in the current working directory. |
| **4** | **Plugins** | Servers bundled inside auto-loaded plugins. |
| **5** | **AgentX** | Configs synced from the `agentx.ai` dashboard. |

### Deduplication Logic
To prevent wasting tokens and resources, AgentX calculates a **Content Signature** for every server:
- `stdio:[command, ...args]`
- `url:unwrap(url)`

If a signature collision occurs, the higher-priority config wins, and the duplicate is suppressed.

---

## 🔒 Security & Governance

### Managed Policy
In enterprise environments, `allowManagedMcpServersOnly` can be enabled. This:
- Disables all user-defined servers.
- Only allows servers explicitly listed in the corporate `policySettings`.

### Server Approval
Before a new MCP server is activated, it often requires a **UI-based approval** (`mcpServerApproval.tsx`). This ensures that users are aware of what tools are being added to their AI context.

### Environment Variable Expansion
MCP configs support environment variables (e.g., `"env": { "API_KEY": "$MY_KEY" }`). These are expanded at runtime using a secure expansion utility that prevents shell injection.

---
*Generated via AgentX Core analysis on 2026-05-12.*
