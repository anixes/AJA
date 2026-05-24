# Sandbox Execution Environment

The Sandbox is the security foundation of Agent, providing a "Shield" that isolates the AI's execution from the host operating system.

## 🛡️ Core Isolation Strategy

Agent utilizes **OS-level sandboxing** rather than heavy containers like Docker. This allows for near-native performance while maintaining strict security boundaries.

### Platform Mechanisms
- **Linux & WSL2**: Uses `bubblewrap` (bwrap) to create unprivileged namespaces.
- **macOS**: Uses native macOS sandboxing APIs.
- **Dependencies**: Requires tools like `bubblewrap` and `socat` on Linux.

---

## ⚙️ Key Components

### 1. The Sandbox Adapter (`sandbox-adapter.ts`)
This acts as a bridge between the external `agent-native/sandbox` and Agent CLI's internal state.
- **Dynamic Configuration**: It subscribes to settings changes and updates the sandbox rules in real-time.
- **Path Resolution**: Handles Agent-specific path syntaxes:
  - `//path`: Absolute from filesystem root.
  - `/path`: Relative to the directory containing the `settings.json` file.

### 2. Filesystem Isolation
The sandbox restricts which directories the AI can see and modify.
- **Auto-Allow**: The current working directory (CWD) and Agent's temp directory are usually allowed.
- **Permission Mapping**: User permissions granted to tools (like `FileEdit`) are automatically converted into sandbox `allowWrite` rules.
- **Lockdown**: Writes to sensitive files like `.agent/settings.json` or `.agent/skills` are **strictly blocked** to prevent the AI from "jailbreaking" its own security settings.

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
By blocking writes to the `.agent` configuration directory within the sandbox, the system ensures that even if an AI agent tries to modify its own `settings.json` to grant itself more power, the operation will fail at the OS level.

### 4. Phase 6: Controlled Execution (Verifiable Dispatch)

With the introduction of **Phase 6**, the sandbox now supports "Verifiable Dispatch" protocols:
- **Mandatory Verification**: Every baton-based worker output is subjected to a structural integrity audit by the `VerificationEngine` before it is allowed to persist to the main memory store.
- **Worker Integrity**: The `WorkerRegistry` tracks reliability scores for every execution, isolating workers that demonstrate erratic behavior or security non-compliance.
- **Handover Validation**: Session transfers between local and mobile (Assistant) are validated via One-Time Codes (OTC), ensuring that the sandbox context remains tied to an authorized user session.

---
*Last Updated: 2026-05-12 (Modernization Phase 6)*
