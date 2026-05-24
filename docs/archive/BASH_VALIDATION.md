# Bash Command Parser and Validation

The Bash parser is the primary command security gate for Assistant on Agent Core. It ensures terminal operations are normalized, classified, explainable, and compliant with user approval rules.

## 🔍 Parsing Strategy

Agent Core is moving toward AST-aware validation, but the current shipped runtime uses a structured normalization pass in `scripts/core/stripper.py` plus TypeScript classification logic in `src/tools/bashTool.ts`.

### 1. Current Normalization Layer
The current runtime:
- Strips leading environment variables and separates safe wrappers such as `sudo`, `timeout`, and `nohup`
- Identifies the root binary and argument tokens
- Extracts shell operators (`|`, `&&`, `||`, `;`, redirections)
- Flags dangerous patterns such as:
  - command substitution `` `...` `` and `$(...)`
  - `curl | bash` / `wget | sh` style network pipes
  - writes into `.ssh`, `/etc`, Windows system paths, and other protected targets
  - blocked env vars like `PATH`, `LD_PRELOAD`, `PYTHONPATH`, and `NODE_OPTIONS`

### 2. Wrapper Transparency
The parser is "wrapper-aware." It can strip and "see through" safe wrappers to validate the underlying command:
- `sudo`
- `nice` / `time` / `timeout`
- `nohup` / `stdbuf`

For example, `sudo rm -rf /` is correctly identified as an `rm` command at the root, rather than just a "sudo" command.

---

## 🛡️ Security Gating

### Environment Variable Sanitization
The runtime now strips dangerous env vars before execution.
- **Blocked**: `PATH`, `LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`, `PYTHONPATH`, `NODE_OPTIONS`, `BASH_ENV`, `ENV`, and related variables trigger a hard deny.
- **Allowed**: Non-sensitive leading env vars are passed through to execution.

### Command Classification
Commands are classified into three behaviors:
- **Allow**: Safe commands (e.g., `ls`, `git status`).
- **Ask**: Potentially destructive or compound commands that require explicit user approval (e.g., `rm`, `npm install`, shell chains with `&&` or `>`).
- **Deny**: Strictly forbidden commands and patterns (e.g., `mkfs`, `dd`, `curl | bash`, SSH trust writes, blocked env vars).

### Attack Mitigations
- **Command Explosion Guard**: Extremely long compound commands are denied once they exceed the segment limit enforced by the classifier.
- **Pending Approval Object**: The runtime stores one risky pending approval at a time in `.agent/runtime-state.json`.
- **Explanation Requirement**: Risky actions include request ID, command preview, action type, reason, risk level, rollback path, expiry, requester source, and dry-run summary.
- **Execution Re-check**: Telegram approvals are revalidated through `FileGuardian` and `CommandStripper` immediately before execution.
- **Immutable Audit**: Approval lifecycle events are appended to `.agent/approval-audit.jsonl`.

### Approval Surfaces

- CLI/runtime: `/approve` or `/deny`
- Dashboard: Approve or Deny buttons
- Telegram: `approve <id>` or `reject <id>`

## 🔜 Planned Upgrade Path

The long-term direction is still an AST-backed parser for shell semantics. The current implementation is a deliberate midpoint:

1. structured stripping and normalization
2. deterministic `Allow / Ask / Deny` classification
3. structured approval objects across CLI, dashboard, and Telegram
4. future AST integration for deeper semantic checks

---
*Generated via RARV analysis on 2026-04-22.*
