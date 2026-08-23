# AJA on a VPS — Runbook

Run `aja serve` (Telegram/Discord + CronScheduler + autonomous goal loop in one
process) on a cheap always-on VPS. Polling adapters are **outbound-only** — no
inbound ports besides SSH.

---

## 1. Host decision

| Host | Price | Specs | Notes |
|------|-------|-------|-------|
| **Hetzner CAX11** ✅ recommended | ~€4/mo | 2 vCPU (AMD), 4 GB RAM, 40 GB, ARM64 | Best value; no idle-reclaim games; simple flat pricing. |
| Oracle Free Tier A1.Flex | $0 | ARM64, up to 4 OCPU/24 GB total free-tier pool — **halved to 2 OCPU / 12 GB from June-2026** | Always-Free, but: (a) capacity for new A1 shapes is often unavailable in popular regions; (b) **idle VMs may be reclaimed** — Oracle stops/reclaims instances deemed idle (low CPU/network over time). Mitigation: upgrade to **Pay As You Go** (PAYG) — still free within the always-free allowance but exempt from idle reclamation and gets better capacity access. |

Both options are ARM64-capable; the Dockerfile builds fine on `linux/arm64`
(Rust/maturin stage included). The CAX11's 4 GB is comfortable; the halved
Oracle shape needs the swap file below if you build on-host.

## 2. Oracle-specific setup

* **Security list**: allow inbound **TCP 22 (SSH) only**. Telegram/Discord are
  outbound polls; baton receive runs on your home worker, not here. No app
  ports.
* **Swap file** (build RAM peak ≈ 2.5 GB during Rust compile):
  ```bash
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```
* **Anti-reclaim**: convert to PAYG billing (free tier limits unchanged).

## 3. Deploy

```bash
# Docker (either host)
curl -fsSL https://get.docker.com | sudo sh

git clone <your-fork-url> aja && cd aja

# Configure secrets
cp docker/.env.vps.example docker/.env
$EDITOR docker/.env   # fill TELEGRAM_BOT_TOKEN + ALLOWED_USER_IDS etc.
# Generate baton secret:
python3 -c "import secrets; print(secrets.token_hex(32))"

docker compose -f docker/docker-compose.vps.yml build
docker compose -f docker/docker-compose.vps.yml up -d
```

Verify:

```bash
docker logs -f aja            # expect startup checks → adapters connected
```

Then send `/status` to your bot from an allowed Telegram account.

## 4. First mission & daily briefing

From Telegram:

```
find the current stable Python version and cite the source
```

Enable the morning briefing (host shell):

```bash
docker compose -f docker/docker-compose.vps.yml exec aja \
  aja briefing enable --at "0 7 * * *"
```

## 5. Updates

```bash
cd aja && git pull
docker compose -f docker/docker-compose.vps.yml down
tar czf ~/aja-backup-$(date +%F).tar.gz \
    $(docker volume inspect aja_aja-data --format '{{.Mountpoint}}')
docker compose -f docker/docker-compose.vps.yml build
docker compose -f docker/docker-compose.vps.yml up -d
```

## 6. Backup / restore

```bash
docker compose -f docker/docker-compose.vps.yml down      # stop first
VOL=$(docker volume inspect aja_aja-data --format '{{.Mountpoint}}')
sudo tar czf ~/aja-data-$(date +%F).tar.gz -C "$VOL" .
docker compose -f docker/docker-compose.vps.yml up -d     # restart
```

Restore: stop, wipe mountpoint, extract tar as root, start.

**What lives inside `AJA_DATA_DIR`** (`/data/aja`):

| Path | Contents |
|------|----------|
| `lancedb/` | episodic memory vectors, cron jobs, gateway sessions |
| `missions/` | mission journals (JSONL shards) |
| `scheduler/` | persisted cron scheduler state |
| `batons/` | Arrow columnar batons (v2 schema) |
| `executions/` | activity execution journals |

## 7. Troubleshooting

* **Stale worker heartbeat** — a home GPU worker went offline mid-mission.
  Check it locally (`aja doctor`); missions resume from journal replay when the
  heartbeat refreshes or you clear the stale row.
* **Telegram denies messages** — remote user not in `TELEGRAM_ALLOWED_USER_IDS`.
  This is fail-safe by design: token without allowlist ⇒ deny everyone. Add your
  numeric id (from @userinfobot), then `docker compose ... restart`.
* **OOM during build** — small hosts die in the Rust stage. Ensure the 4 GB
  swap exists (§2) and/or cap compile parallelism:
  ```bash
  docker compose -f docker/docker-compose.vps.yml build \
    --build-arg CARGO_BUILD_JOBS=2   # requires adding the ARG to the builder stage
  ```
  Alternative: build elsewhere (`docker buildx build --platform linux/arm64`)
  and push/load the image.
