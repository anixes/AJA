# Sandbox Execution Environment

The Sandbox is the security foundation of AgentX, providing a "Shield" that isolates the AI's execution from the host operating system.

## 🛡️ Core Isolation Strategy

AgentX utilizes **OS-level sandboxing** rather than heavy containers like Docker. This allows for near-native performance while maintaining strict security boundaries.

### Platform Mechanisms
- **Linux & WSL2**: Uses `bubblewrap` (bwrap) to create unprivileged namespaces.
- **macOS**: Uses native macOS sandboxing APIs.
- **Dependencies**: Requires tools like `bubblewrap` and `socat` on Linux.

---

## ⚙️ Key Components

### 1. The Sandbox Adapter (`sandbox-adapter.ts`)
This acts as a bridge between the external `agentx-native/sandbox` and AgentX CLI's internal state.
- **Dynamic Configuration**: It subscribes to settings changes and updates the sandbox rules in real-time.
- **Path Resolution**: Handles AgentX-specific path syntaxes:
  - `//path`: Absolute from filesystem root.
  - `/path`: Relative to the directory containing the `settings.json` file.

### 2. Filesystem Isolation
The sandbox restricts which directories the AI can see and modify.
- **Auto-Allow**: The current working directory (CWD) and AgentX's temp directory are usually allowed.
- **Permission Mapping**: User permissions granted to tools (like `FileEdit`) are automatically converted into sandbox `allowWrite` rules.
- **Lockdown**: Writes to sensitive files like `.agentx/settings.json` or `.agentx/skills` are **strictly blocked** to prevent the AI from "jailbreaking" its own security settings.

### 3. Network Isolation
The sandbox acts as a firewall for `WebFetch` operations.
- **Domain Allow-lists**: Only domains explicitly granted via `permissions.allow` are accessible.
- **Managed Mode**: In enterprise settings, `allowManagedDomainsOnly` can be enabled, restricting the AI to a hard-coded corporate whitelist.

---

## 🔒 Security Hardening

### Bare-Git Repo Protection
A critical security feature I found is the **Bare-Git Scrubbing**. If an attacker plants git control files (like `HEAD` or `objects`) in a directory, a subsequent unsandboxed `git` call could be tricked into executing malicious code. The sandbox adapter:
1. Detects if these files were "planted" during a sandboxed session.
2. Synchronously scrubs them using `rmSync` before the main process can interact with them.

### Settings Protection
By blocking writes to the `.agentx` configuration directory within the sandbox, the system ensures that even if an AI agent tries to modify its own `settings.json` to grant itself more power, the operation will fail at the OS level.

---
*Generated via AgentX analysis on 2026-04-22.*
