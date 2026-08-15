#!/usr/bin/env bash
# ==============================================================================
# AJA VPS Automated Installer & Production Setup Script
# ==============================================================================
# Supported OS: Ubuntu 22.04+, Debian 12+, Fedora 38+, CentOS Stream / RHEL 9+
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

INSTALL_DIR="/opt/aja"
AJA_USER="aja"
AJA_GROUP="aja"
REPO_URL="${AJA_REPO_URL:-https://github.com/anixes/AJA.git}"
BRANCH="${AJA_BRANCH:-native-worker-3}"

log_info() {
    echo -e "${BLUE}[AJA VPS INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[AJA VPS SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[AJA VPS WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[AJA VPS ERROR]${NC} $1"
}

# Ensure running as root or with sudo
if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root or via sudo."
   exit 1
fi

log_info "Starting AJA VPS automated provisioning..."

# 1. Detect Package Manager & Install System Prerequisites
log_info "Step 1: Installing system prerequisites..."
if command -v apt-get &>/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y --no-install-recommends \
        git curl build-essential pkg-config libssl-dev \
        python3 python3-pip python3-venv python3-dev \
        logrotate ufw
elif command -v dnf &>/dev/null; then
    dnf install -y \
        git curl make gcc gcc-c++ pkgconfig openssl-devel \
        python3 python3-pip python3-devel logrotate ufw
elif command -v yum &>/dev/null; then
    yum install -y \
        git curl make gcc gcc-c++ pkgconfig openssl-devel \
        python3 python3-pip python3-devel logrotate
elif command -v pacman &>/dev/null; then
    pacman -Sy --noconfirm \
        git curl base-devel openssl \
        python python-pip logrotate
else
    log_warn "Unknown package manager. Please ensure Python 3.11+, Git, and C build tools are installed."
fi

# 2. Check Python Version (>= 3.11)
log_info "Step 2: Validating Python version..."
PYTHON_BIN=""
for py in python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        VER=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 11 ]]; then
            PYTHON_BIN=$(command -v "$py")
            log_success "Found compatible Python: $PYTHON_BIN (version $VER)"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    log_error "Python 3.11 or higher is required. Please install Python 3.11+ and re-run."
    exit 1
fi

# 3. Create Dedicated AJA System User & Directories
log_info "Step 3: Creating dedicated '$AJA_USER' system user and directory structure..."
if ! id -u "$AJA_USER" &>/dev/null; then
    useradd --system --shell /bin/bash --home-dir "$INSTALL_DIR" --create-home "$AJA_USER"
    log_success "Created system user '$AJA_USER'."
else
    log_info "User '$AJA_USER' already exists."
fi

mkdir -p "$INSTALL_DIR" "/var/log/aja"
chown -R "$AJA_USER:$AJA_GROUP" "$INSTALL_DIR" "/var/log/aja"

# 4. Clone or Update Repository
log_info "Step 4: Syncing AJA repository to $INSTALL_DIR..."
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    sudo -u "$AJA_USER" git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    log_success "Repository cloned."
else
    cd "$INSTALL_DIR"
    sudo -u "$AJA_USER" git fetch origin
    sudo -u "$AJA_USER" git checkout "$BRANCH"
    sudo -u "$AJA_USER" git pull origin "$BRANCH" || true
    log_success "Repository updated."
fi

cd "$INSTALL_DIR"

# 5. Set up Python Virtual Environment & Install Dependencies
log_info "Step 5: Setting up Python virtual environment and building dependencies..."
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    sudo -u "$AJA_USER" "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
fi

sudo -u "$AJA_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip setuptools wheel

# Install Rust toolchain for user if maturin compilation is needed
if ! command -v cargo &>/dev/null && ! sudo -u "$AJA_USER" command -v cargo &>/dev/null; then
    log_info "Installing Rust toolchain for native module compilation..."
    sudo -u "$AJA_USER" curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sudo -u "$AJA_USER" sh -s -- -y --default-toolchain stable --profile minimal
fi

# Install AJA into the virtualenv
log_info "Installing AJA packages into venv..."
sudo -u "$AJA_USER" bash -c "
    export PATH=\"\$HOME/.cargo/bin:\$PATH\"
    $INSTALL_DIR/venv/bin/pip install -e '.[all]'
"
log_success "AJA installed successfully."

# 6. Configure Environment & Security Settings
log_info "Step 6: Configuring environment and security parameters..."
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    if [[ -f "$INSTALL_DIR/.env.example" ]]; then
        sudo -u "$AJA_USER" cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    else
        sudo -u "$AJA_USER" touch "$INSTALL_DIR/.env"
    fi
fi

# Restrict .env permissions
chmod 600 "$INSTALL_DIR/.env"
chown "$AJA_USER:$AJA_GROUP" "$INSTALL_DIR/.env"

# Ensure .data folder exists with restricted permissions
mkdir -p "$INSTALL_DIR/.data"
chmod 700 "$INSTALL_DIR/.data"
chown -R "$AJA_USER:$AJA_GROUP" "$INSTALL_DIR/.data"

# 7. Install Systemd Service Unit & Logrotate
log_info "Step 7: Installing systemd service unit and logrotate..."
cp "$INSTALL_DIR/scripts/vps/aja.service" /etc/systemd/system/aja.service
chmod 644 /etc/systemd/system/aja.service

if [[ -f "$INSTALL_DIR/scripts/vps/logrotate.aja" ]]; then
    cp "$INSTALL_DIR/scripts/vps/logrotate.aja" /etc/logrotate.d/aja
    chmod 644 /etc/logrotate.d/aja
fi

# Install helper CLI symlink
cp "$INSTALL_DIR/scripts/vps/aja-ctl" /usr/local/bin/aja-ctl
chmod 755 /usr/local/bin/aja-ctl

# Link aja executable into /usr/local/bin
ln -sf "$INSTALL_DIR/venv/bin/aja" /usr/local/bin/aja

# Reload systemd
systemctl daemon-reload
systemctl enable aja.service

# 8. Run Diagnostic Doctor Check
log_info "Step 8: Running AJA diagnostic health check..."
sudo -u "$AJA_USER" "$INSTALL_DIR/venv/bin/aja" doctor || true

# 9. Completion Summary
echo ""
echo -e "${GREEN}========================================================================${NC}"
echo -e "${GREEN}             AJA VPS DEPLOYMENT COMPLETED SUCCESSFULLY!                ${NC}"
echo -e "${GREEN}========================================================================${NC}"
echo ""
echo -e "  ${BLUE}Service Status:${NC}    systemctl status aja"
echo -e "  ${BLUE}Start Service:${NC}     systemctl start aja (or: aja-ctl start)"
echo -e "  ${BLUE}Live Logs:${NC}         journalctl -u aja -f (or: aja-ctl logs)"
echo -e "  ${BLUE}Management CLI:${NC}    aja-ctl <status|start|stop|restart|logs|update|doctor>"
echo -e "  ${BLUE}Config Files:${NC}      $INSTALL_DIR/.env  and  $INSTALL_DIR/aja.json"
echo ""
echo -e "${YELLOW}Next Step:${NC} Add your TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID to"
echo -e "           ${INSTALL_DIR}/.env, then run: ${GREEN}aja-ctl restart${NC}"
echo ""
