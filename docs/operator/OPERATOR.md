# AJA Operator Manual

AJA is an ambient **Autonomous Cognitive Agent OS** and local-first durable execution runtime designed to manage autonomous multi-agent missions. This manual outlines how to operate, monitor, and maintain an AJA deployment.

---

## 1. Operating Modes & Workspaces

AJA supports both **Ambient Host Mode** (managing the overall VPS, Docker daemons, system triage) and **Multi-Workspace Mode** (isolated projects).

### Workspace CLI Commands (`aja ws`)

```bash
# 1. Register projects in the central registry (~/.aja/workspaces.json)
aja ws add ~/projects/frontend-app --name frontend
aja ws add /var/www/api-backend --name backend

# 2. List all registered workspaces and their active state
aja ws list

# 3. Switch the default active workspace context
aja ws use frontend

# 4. Dispatch a mission directly to a specific workspace
aja ws run backend "Add database healthcheck endpoint"

# 5. Check Kernel Priority Scheduler status
aja ws status
```

---

## 2. Cognitive Memory & Procedural Skills

AJA’s cognitive engine relies on the **CoALA Tripartite Memory Stack**:

### Adding Custom Procedural Skills (`~/.aja/skills/`)
To give AJA specialized workflows, create a directory under `~/.aja/skills/<skill-name>/` following the **agentskills.io** specification:

```
~/.aja/skills/vps-backup/
├── SKILL.md           # Instructions with YAML frontmatter
└── backup.py          # Executable Python script
```

Example `SKILL.md`:
```markdown
---
name: vps-backup
description: Performs an automated tarball backup of database and state files.
---
# VPS Backup Skill
Run `python backup.py` to create a timestamped snapshot of active databases.
```
AJA’s `CognitiveMemoryManager` dynamically indexes skills and injects them into the agent's contextual prompt when relevant.

---

## 3. Specialist Personas & Autonomous Missions

AJA automatically routes user missions to **Magentic-One Specialists**:

* **`SysAdminSpecialist`**: Dispatched for host health, Docker inspections, CPU/RAM triage, and port status.
  ```bash
  aja run "Inspect docker containers and alert on any unhealthy restart loops"
  ```
* **`WebResearchSpecialist`**: Dispatched for documentation retrieval and technical synthesis.
  ```bash
  aja run "Search recent FastAPI v0.115 breaking changes and summarize migration steps"
  ```
* **`CodeEngineerSpecialist`**: Dispatched for codebase refactoring, testing, and implementation.
  ```bash
  aja run "Refactor database connection pool and run pytest suite"
  ```

---

## 4. Diagnostics & Maintenance

### System Diagnostics
```bash
aja doctor
```
Verifies environment readiness: Python 3.12, PyO3 native extensions, LanceDB vector tables, disk usage, active workspaces, and API token connectivity.

### Telegram Remote Operations
When running as a daemon on a VPS (`scripts/vps/aja-ctl start`), operators can interact remotely via Telegram:
* `/workspaces` - List registered projects.
* `/switch <name>` - Switch active workspace.
* `@backend <mission>` - Run an urgent mission on the backend workspace.
* `/status` - View scheduler queue and resource utilization.

