# Bash Security Patterns: Stripping & Validation

AgentX uses a multi-layered approach to ensure that shell commands are safe before execution. This document deconstructs the patterns found in `agentx_guard.py` and the core security layers.

## 🛡️ The Three-Layer Defense

### Layer 1: De-noising (The Stripper)
The goal is to find the **True Binary**. An attacker might try to hide a command:
`PORT=3000 sudo rm -rf /`
- AgentX's `stripAllLeadingEnvVars()` logic removes `PORT=3000`.
- It then identifies `sudo` as a "safe wrapper" and looks at the next word.
- It finally arrives at `rm`, which triggers the "Ask/Deny" logic.

### Layer 2: Normalization
Commands that use relative paths are dangerous because they depend on the current working directory (CWD).
- `cd ../../etc && cat shadow`
- AgentX normalizes the `cd` target. If the resulting path is a protected system directory, it blocks the command chain entirely.

### Layer 3: Pattern Validation
Even after stripping, the command might contain "Dangerous Patterns" in its arguments.
- **Redirection**: `echo "malicious" > ~/.ssh/authorized_keys`
- **Shell Escapes**: Using backticks `` ` `` or `$(...)` inside a seemingly safe command.
- **Network Pipes**: `curl ... | bash` (Strictly validated or blocked).

---

## 🛠️ Implementation Strategy

To maintain security in AgentX, we follow this sequence:

1.  **Regex-based Env Stripping**: Identify strings matching `^[A-Z_]+=[^\s]+`.
2.  **Wrapper Look-through**: Maintain an allow-list of wrappers (`sudo`, `nice`, `timeout`, `time`, `nohup`).
3.  **AST Parsing**: For complex chains (pipes/semicolons), parse the full tree. If ANY node in the tree is destructive, flag the entire mission.

---
*Generated via AgentX Core analysis on 2026-05-12.*
