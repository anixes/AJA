---
name: security-review
description: How to apply, audit, and extend AJA's security controls — CommandGuard, PermissionEngine, and path-boundary enforcement.
---
# Security Review

AJA has three overlapping security layers. Understand all three before modifying
any execution or permission logic.

## Layer 1 — CommandGuard (`aja.security.command_guard`)

Classifies every shell command before execution. Returns a decision of
`allow`, `ask`, or `deny` with a risk level and reasons.

```python
from aja.security.command_guard import classify_command

result = classify_command("rm -rf /")
print(result["decision"])    # "deny"
print(result["reasons"])     # ["Destructive recursive deletion of root"]
```

`deny` decisions are hard-blocked — the shell executor never spawns the process.
`ask` decisions are escalated to the `PermissionEngine`.

## Layer 2 — PermissionEngine (`aja.security.permissions`)

Enforces hierarchical scope policies. All activity execution flows through
`ActivityRuntime._authorize()` before the tool runs.

```python
from aja.security.permissions import PermissionEngine, PermissionPolicy

# Custom policy: allow reads, deny everything else
policy = PermissionPolicy(scopes={
    "shell.read":        "allow",
    "shell.write":       "deny",
    "shell.destructive": "deny",
    "python.*":          "allow",
})
engine = PermissionEngine(policy)

result = engine.authorize("shell.write", reason="agent wants to create a file")
print(result.allowed)   # False
```

Every decision is journalled as `PERMISSION_GRANTED` or `PERMISSION_DENIED` in
the `MissionJournal` — fully auditable and replay-safe.

### Default scope map

| Scope                | Default decision |
|----------------------|-----------------|
| `shell.*`            | `allow`         |
| `python.*`           | `allow`         |
| `mcp.*`              | `ask`           |
| `browser.read`       | `allow`         |
| `browser.navigate`   | `ask`           |
| `browser.interact`   | `ask`           |
| `desktop.interact`   | `ask`           |
| `fs.read.global`     | `ask`           |
| `fs.write.global`    | `ask`           |

Override in `aja.json` under `permission_policy.scopes`.

## Layer 3 — Path boundary enforcement (`NativeToolRegistry._validate_path`)

File-operation tools (`read_file`, `write_file`, `delete_path`, etc.) reject paths
outside `PROJECT_ROOT` unless `allow_out_of_bounds_paths: true` is set in
`aja.json` (default: `false`). Out-of-bounds access escalates to
`PermissionEngine.authorize("fs.read.global")`.

**Never set `allow_out_of_bounds_paths: true` in production.**

## Auditing security decisions

```bash
# Review all PERMISSION_DENIED events for a mission
python -c "
from aja.runtime.mission_journal import MissionJournal
j = MissionJournal('M-XXXXXX')
denied = [e for e in j.read_events() if e['event_type'] == 'PERMISSION_DENIED']
for e in denied: print(e)
"
```

## Adding a new blocked command pattern

Edit `libs/aja-core/aja/security/command_guard.py` and add your pattern to the
appropriate risk tier. Then add a regression test in
`tests/python/unit/test_agent_security_audit.py`.

## Rules
- All three layers must remain active simultaneously — disabling one does not make the others stronger.
- `CommandGuard` fires on raw command strings; `PermissionEngine` fires on resolved scopes. Both must agree for execution to proceed.
- Security tests must set `allow_out_of_bounds_paths = False` explicitly in `setUp` — never rely on config defaults in test isolation.
- Any change to permission scopes or CommandGuard patterns requires a corresponding invariant update in `tests/python/invariants.py`.
