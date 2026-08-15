# AJA VPS & Cloud Deployment Guide

This guide provides step-by-step instructions for deploying **AJA** onto a Linux Virtual Private Server (VPS) such as **DigitalOcean, Hetzner, AWS EC2, Linode, or Vultr** as a secure, 24/7 always-on autonomous coding and DevOps assistant.

---

## 1. System Requirements & Architecture

* **Operating System**: Ubuntu 22.04 LTS+, Debian 12+, Fedora 38+, or CentOS Stream / RHEL 9+.
* **Minimum Specifications**: 1 vCPU, 1 GB RAM, 15 GB SSD storage (runs smoothly on a standard $4–$6/month droplet).
* **Recommended Specifications**: 2 vCPUs, 2 GB RAM, 25 GB SSD storage.

```
 ┌─────────────────────────────────────────────────────────────┐
 │                      YOUR LINUX VPS                         │
 │                                                             │
 │  ┌───────────────────────────────────────────────────────┐  │
 │  │ systemd service unit: /etc/systemd/system/aja.service │  │
 │  │ (Runs as unprivileged system user 'aja')              │  │
 │  └──────────────────────────┬────────────────────────────┘  │
 │                             ▼                               │
 │  ┌──────────────────┐               ┌────────────────────┐  │
 │  │ Gateway Server   │ <───────────> │ Swarm Autonomous   │  │
 │  │ (Telegram Client)│               │ Multi-Agent Loop   │  │
 │  └────────┬─────────┘               └─────────┬──────────┘  │
 │           │                                   │             │
 │           ▼                                   ▼             │
 │  ┌───────────────────────────────────────────────────────┐  │
 │  │ Security Sandbox: CommandGuard + Permission Engine    │  │
 │  │ (Blocks destructive commands, restricts to workspace) │  │
 │  └───────────────────────────────────────────────────────┘  │
 └─────────────────────────────────────────────────────────────┘
```

---

## 2. Option A: One-Command Automated Installation (Recommended)

Run the automated VPS provisioning script as `root` or via `sudo`:

```bash
curl -sSL https://raw.githubusercontent.com/anixes/AJA/native-worker-3/scripts/vps/install.sh | sudo bash
```

### What the installer automates:
1. Installs system build tools (`git`, `curl`, `gcc`, `libssl-dev`, `python3-venv`).
2. Creates an isolated system user and group (`aja:aja`).
3. Clones the repository to `/opt/aja` and configures a dedicated Python virtualenv.
4. Compiles the native Rust acceleration module via Maturin.
5. Installs the production `systemd` service (`/etc/systemd/system/aja.service`).
6. Configures automatic log rotation (`/etc/logrotate.d/aja`).
7. Creates the `aja-ctl` management CLI in `/usr/local/bin/aja-ctl`.

---

## 3. Configuration & Telegram Pairing

After the installation completes, configure your credentials in `/opt/aja/.env` and `/opt/aja/aja.json`:

### 1. Telegram Bot Credentials
1. Open Telegram and message [@BotFather](https://t.me/botfather) to create a new bot and obtain your `BOT_TOKEN`.
2. Message [@userinfobot](https://t.me/userinfobot) to get your numeric Telegram `User ID` (e.g. `123456789`).
3. Edit `/opt/aja/.env`:
   ```bash
   sudo nano /opt/aja/.env
   ```
   Add your credentials:
   ```ini
   TELEGRAM_TOKEN="1234567890:ABC-DEF-GHI-..."
   TELEGRAM_ALLOWED_USER_ID="123456789"
   ```

### 2. Configure LLM Provider in `/opt/aja/aja.json`
```json
{
  "operating_mode": "online",
  "offline_mode": false,
  "swarm_settings": {
    "models": {
      "planner": "copilot:gpt-4o",
      "worker": "copilot:gpt-4o-mini",
      "critic": "copilot:gpt-4o-mini"
    },
    "allow_out_of_bounds_paths": false,
    "sandbox_mode": "local"
  }
}
```

### 3. Restart the Service
```bash
sudo aja-ctl restart
```

---

## 4. VPS Service Management (`aja-ctl`)

The `aja-ctl` tool provides management controls:

| Command | Action |
| :--- | :--- |
| `aja-ctl status` | Shows systemd service status and runs `aja doctor` diagnostics |
| `aja-ctl logs` | Streams live unified application and journald logs |
| `aja-ctl restart` | Gracefully restarts the AJA background service |
| `aja-ctl stop` | Stops the AJA background daemon |
| `aja-ctl start` | Starts the AJA background daemon |
| `aja-ctl update` | Pulls latest git commits, rebuilds native extensions, and restarts |
| `aja-ctl doctor` | Runs full system health check |
| `aja-ctl shell` | Drops into an interactive bash shell as the `aja` user in `/opt/aja` |

---

## 5. Option B: Docker Container Deployment

If you prefer containerized deployment, use the production Docker Compose configuration:

```bash
# 1. Clone repository
git clone https://github.com/anixes/AJA.git /opt/aja && cd /opt/aja

# 2. Configure .env
cp .env.example .env
nano .env

# 3. Start container in background
docker compose -f docker/docker-compose.prod.yml up -d --build
```

---

## 6. Security Hardening on VPS

AJA implements multiple layers of defense to safeguard your VPS:

1. **Unprivileged Execution**: The systemd service runs strictly under user `aja` with `NoNewPrivileges=true`, `PrivateTmp=true`, and `ProtectSystem=full`.
2. **Command Guard**: Intercepts and denies destructive shell commands (`mkfs`, `dd`, `fdisk`, `format`, `rm -rf /`, `curl ... | bash`).
3. **Workspace Boundary**: When `allow_out_of_bounds_paths = false`, all file operations (`read_file`, `write_file`, `delete_path`) outside the project root are rejected.
4. **Telegram Authorization Gate**: Only requests from `TELEGRAM_ALLOWED_USER_ID` are processed. All unauthorized user messages are ignored.
5. **Interactive High-Risk Approvals**: Any potentially dangerous command requires explicit approval via Telegram inline buttons before execution.
