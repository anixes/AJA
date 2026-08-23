#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════
# AJA Interactive Quickstart (Linux / macOS)
# Clone → venv → install → configure Telegram → doctor → chat.
# Idempotent: safe to re-run; existing artifacts are detected and reused.
# ══════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$REPO_ROOT/.venv"
ENV_FILE="$REPO_ROOT/.env"

ok()   { printf '\033[32m[✓]\033[0m %s\n' "$1"; }
step() { printf '\033[36m[→]\033[0m %s\n' "$1"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$1"; }
die()  { warn "$1" >&2; exit 1; }

on_error() {
  local exit_code=$?
  warn "Quickstart failed at line $LINENO (exit code $exit_code)."
  echo "    Common fixes:"
  echo "    - Delete .venv/ and re-run to rebuild the environment."
  echo "    - Ensure network access for pip."
  echo "    - Run 'python -m aja doctor' for diagnostics."
  exit "$exit_code"
}
trap on_error ERR

echo ""
echo "════════════════ AJA QUICKSTART ════════════════"
echo ""

# ── a) Python >= 3.11 ──────────────────────────────────────────────────────
step "Checking Python version..."
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 not found. Install Python 3.11+ from https://www.python.org/downloads/"
fi
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)')"
if [ "$PY_OK" != "1" ]; then
  die "Python $PY_VERSION found, but 3.11+ is required. Install from https://www.python.org/downloads/"
fi
ok "Python $PY_VERSION"

# ── b) git available ───────────────────────────────────────────────────────
step "Checking git..."
command -v git >/dev/null 2>&1 || die "git not found. Install git: https://git-scm.com/downloads"
ok "git $(git --version | awk '{print $3}')"

cd "$REPO_ROOT"

# ── c) Create/reuse .venv ──────────────────────────────────────────────────
SKIP_VENV=0
if [ -d "$VENV_DIR" ]; then
  if [ -x "$VENV_DIR/bin/python" ]; then
    read -r -p "[?] Existing .venv found. Reuse it? [Y/n] " reuse_venv
    case "$reuse_venv" in
      n|N) step "Recreating .venv..."; rm -rf "$VENV_DIR"; python3 -m venv "$VENV_DIR" ;;
      *)   SKIP_VENV=1 ;;
    esac
  else
    warn "Existing .venv lacks bin/python — recreating."
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
else
  step "Creating virtual environment (.venv)..."
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "Virtual environment active ($(python --version))"

# ── d) pip install -e ".[telegram]" ────────────────────────────────────────
if [ "$SKIP_VENV" = "1" ]; then
  read -r -p "[?] Reinstall package (pip install -e)? [y/N] " reinstall
  case "$reinstall" in
    y|Y) : ;;
    *)   ok "Skipping installation (reusing existing)."; SKIP_INSTALL=1 ;;
  esac
fi
if [ "${SKIP_INSTALL:-0}" != "1" ]; then
  step "Installing AJA (editable, telegram extras) — this can take a few minutes..."
  pip install --upgrade pip >/dev/null
  pip install -e ".[telegram]"
  ok "AJA installed"
fi

# ── e) .env: reuse or create from template ─────────────────────────────────
CREATE_ENV=1
if [ -f "$ENV_FILE" ]; then
  read -r -p "[?] Existing .env found. Reuse it? [Y/n] " reuse_env
  case "$reuse_env" in
    n|N)
      cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
      step "Backed up old .env; creating a fresh one."
      ;;
    *)
      CREATE_ENV=0
      ok "Reusing existing .env"
      ;;
  esac
fi
if [ "$CREATE_ENV" = "1" ]; then
  step "Creating .env from template..."
  cat > "$ENV_FILE" <<'EOF'
# ══════════════════════════════════════════════════════════════
# AJA local environment (Telegram-first quickstart)
# NEVER commit this file.
# ══════════════════════════════════════════════════════════════

# REQUIRED — bot token from @BotFather on Telegram
TELEGRAM_BOT_TOKEN=

# REQUIRED — your numeric Telegram user id(s) from @userinfobot.
# Fail-safe: without an allowlist, every remote user is DENIED.
TELEGRAM_ALLOWED_USER_IDS=

# OPTIONAL — GitHub Copilot token as LLM provider (gh CLI token works).
# COPILOT_GITHUB_TOKEN=

# OPTIONAL — shared HMAC secret for fleet/baton transfers between hosts.
# Generate: python -c "import secrets; print(secrets.token_hex(32))"
# AJA_BATON_SECRET=

# OPTIONAL — embeddings backend (auto/onnx/mock). Leave unset for auto.
# AJA_EMBEDDING_BACKEND=

# OPTIONAL — scheduled-mission and worker timeouts (seconds).
# AJA_JOB_TIMEOUT_S=600
# AJA_WORKER_TIMEOUT_S=600
# AJA_WORKER_RUN_TESTS=0

# OPTIONAL — Google Calendar integration (see docs/operator/CALENDAR.md).
# GOOGLE_CALENDAR_CLIENT_SECRET=
EOF
  ok ".env created"
fi

# ── f/g) Prompt credentials (skip when reusing a filled .env) ───────────────
PROMPT_CREDS=1
if [ "$CREATE_ENV" = "0" ]; then
  CURRENT_TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=.+' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  CURRENT_IDS="$(grep -E '^TELEGRAM_ALLOWED_USER_IDS=.+' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
  if [ -n "$CURRENT_TOKEN" ] && [ -n "$CURRENT_IDS" ]; then
    PROMPT_CREDS=0
    ok "Telegram credentials already configured in .env"
  fi
fi

if [ "$PROMPT_CREDS" = "1" ]; then
  echo ""
  echo "────────────── Telegram configuration ──────────────"
  BOT_TOKEN=""
  while true; do
    read -r -p "[?] Paste TELEGRAM_BOT_TOKEN (from @BotFather): " BOT_TOKEN
    if [ -z "$BOT_TOKEN" ]; then
      warn "Token cannot be empty."
    elif ! [[ "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
      warn "That doesn't look like a bot token (expected format: digits:alphanumeric). Try again."
    else
      break
    fi
  done
  USER_IDS=""
  while [ -z "$USER_IDS" ]; do
    echo "    Get YOUR numeric user id by messaging @userinfobot on Telegram."
    read -r -p "[?] Paste TELEGRAM_ALLOWED_USER_IDS (comma-separated ids): " USER_IDS
    [ -z "$USER_IDS" ] && warn "At least one id is REQUIRED — the gateway denies everyone otherwise."
  done
  if grep -q '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE"; then
    sed -i.bak "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$BOT_TOKEN|" "$ENV_FILE"
  else
    echo "TELEGRAM_BOT_TOKEN=$BOT_TOKEN" >> "$ENV_FILE"
  fi
  if grep -q '^TELEGRAM_ALLOWED_USER_IDS=' "$ENV_FILE"; then
    sed -i.bak "s|^TELEGRAM_ALLOWED_USER_IDS=.*|TELEGRAM_ALLOWED_USER_IDS=$USER_IDS|" "$ENV_FILE"
  else
    echo "TELEGRAM_ALLOWED_USER_IDS=$USER_IDS" >> "$ENV_FILE"
  fi
  rm -f "$ENV_FILE.bak"
  ok "Credentials written to .env"
fi

# ── h) doctor gate ─────────────────────────────────────────────────────────
step "Running diagnostics (aja doctor)..."
if python -m aja doctor; then
  ok "Doctor passed"
else
  die "Doctor reported problems. Fix the issues above, then re-run this script."
fi

# ── i) Success box ─────────────────────────────────────────────────────────
echo ""
printf '\033[32m'
cat <<'EOF'
╔══════════════════════════════════════════════════════╗
║           AJA IS READY — LET'S CHAT                  ║
╠══════════════════════════════════════════════════════╣
║  Next steps:                                         ║
║                                                      ║
║   1. Start everything:        aja serve              ║
║   2. Open Telegram and send                          ║
║      a message to your bot.                          ║
║                                                      ║
║  Useful commands:                                    ║
║    aja doctor     — validate setup                   ║
║    aja ws         — manage workspaces                ║
║    aja run "..."  — run a mission locally            ║
╚══════════════════════════════════════════════════════╝
EOF
printf '\033[0m'
