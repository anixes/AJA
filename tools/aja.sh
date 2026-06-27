#!/usr/bin/env bash
# AJA 24/7 Autonomous System Launcher (Linux / macOS)
# Starts the Telegram Gateway and Autonomous Worker as supervised background
# processes, automatically restarting either component if it exits unexpectedly.
#
# Usage:
#   bash tools/aja.sh
#
# Required environment variable:
#   TELEGRAM_BOT_TOKEN  — your Telegram bot token
#
# Optional:
#   POLL_INTERVAL   — seconds between health checks (default: 30)
#   MAX_RESTARTS    — max restarts per component, 0 = unlimited (default: 10)

set -euo pipefail

POLL_INTERVAL="${POLL_INTERVAL:-30}"
MAX_RESTARTS="${MAX_RESTARTS:-10}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

GATEWAY_PID=""
WORKER_PID=""
GATEWAY_RESTARTS=0
WORKER_RESTARTS=0

# --- Colours ------------------------------------------------------------------
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; GRAY='\033[0;37m'; NC='\033[0m'

echo -e "${CYAN}--------------------------------------------------${NC}"
echo -e "${CYAN}   AJA: Autonomous Gateway & Execution Loop${NC}"
echo -e "${CYAN}--------------------------------------------------${NC}"

# --- Pre-flight checks --------------------------------------------------------

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    echo -e "${RED}ERROR: TELEGRAM_BOT_TOKEN is not set.${NC}"
    echo -e "${YELLOW}  Export it before running: export TELEGRAM_BOT_TOKEN=your-token${NC}"
    exit 1
fi

cd "$ROOT"

# --- Helpers ------------------------------------------------------------------

start_gateway() {
    python -m aja.gateway.server >> "$ROOT/logs/gateway.log" 2>&1 &
    GATEWAY_PID=$!
    echo -e "${GREEN}[+] Started AJAGateway (PID: $GATEWAY_PID)${NC}"
}

start_worker() {
    python -m aja.runtime.autonomous_loop >> "$ROOT/logs/worker.log" 2>&1 &
    WORKER_PID=$!
    echo -e "${GREEN}[+] Started AJAWorker  (PID: $WORKER_PID)${NC}"
}

is_running() {
    local pid="$1"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# --- Clean shutdown on Ctrl-C / SIGTERM ---------------------------------------

shutdown() {
    echo -e "\n${YELLOW}[*] Shutting down AJA...${NC}"
    [ -n "$GATEWAY_PID" ] && kill "$GATEWAY_PID" 2>/dev/null || true
    [ -n "$WORKER_PID"  ] && kill "$WORKER_PID"  2>/dev/null || true
    wait 2>/dev/null || true
    echo -e "${GREEN}[*] AJA stopped cleanly.${NC}"
    exit 0
}
trap shutdown SIGINT SIGTERM

# --- Ensure log directory exists ----------------------------------------------

mkdir -p "$ROOT/logs"

# --- Launch both components ---------------------------------------------------

start_gateway
start_worker

echo ""
echo -e "${GREEN}AJA is LIVE. Monitoring every ${POLL_INTERVAL}s.${NC}"
echo -e "${GRAY}  Logs: $ROOT/logs/gateway.log  |  $ROOT/logs/worker.log${NC}"
echo -e "${GRAY}  Kill: kill $BASHPID  or  Ctrl-C${NC}"
echo -e "${CYAN}--------------------------------------------------${NC}"

# --- Supervised monitor loop --------------------------------------------------

while true; do
    sleep "$POLL_INTERVAL"

    # Gateway
    if ! is_running "$GATEWAY_PID"; then
        if [ "$MAX_RESTARTS" -gt 0 ] && [ "$GATEWAY_RESTARTS" -ge "$MAX_RESTARTS" ]; then
            echo -e "${RED}[!] AJAGateway exceeded $MAX_RESTARTS restarts — giving up.${NC}"
        else
            echo -e "${YELLOW}[!] AJAGateway stopped (restarts: $GATEWAY_RESTARTS). Restarting...${NC}"
            start_gateway
            GATEWAY_RESTARTS=$((GATEWAY_RESTARTS + 1))
        fi
    fi

    # Worker
    if ! is_running "$WORKER_PID"; then
        if [ "$MAX_RESTARTS" -gt 0 ] && [ "$WORKER_RESTARTS" -ge "$MAX_RESTARTS" ]; then
            echo -e "${RED}[!] AJAWorker exceeded $MAX_RESTARTS restarts — giving up.${NC}"
        else
            echo -e "${YELLOW}[!] AJAWorker stopped (restarts: $WORKER_RESTARTS). Restarting...${NC}"
            start_worker
            WORKER_RESTARTS=$((WORKER_RESTARTS + 1))
        fi
    fi
done
