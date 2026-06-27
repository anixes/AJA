# AJA 24/7 Autonomous System Launcher (Windows / PowerShell)
# Starts the Telegram Gateway and Autonomous Worker as supervised background
# jobs, automatically restarting either component if it exits unexpectedly.
#
# Usage:
#   .\tools\aja.ps1
#
# Required environment variable:
#   TELEGRAM_BOT_TOKEN  — your Telegram bot token

param(
    [int]$PollIntervalSeconds = 30,
    [int]$MaxRestarts         = 10   # per component; 0 = unlimited
)

$Root = Get-Location

Write-Host "--------------------------------------------------" -ForegroundColor Cyan
Write-Host "   AJA: Autonomous Gateway & Execution Loop" -ForegroundColor Cyan
Write-Host "--------------------------------------------------" -ForegroundColor Cyan

# --- Pre-flight checks --------------------------------------------------------

if (-not $Env:TELEGRAM_BOT_TOKEN) {
    Write-Host "ERROR: TELEGRAM_BOT_TOKEN is not set." -ForegroundColor Red
    Write-Host "  Export it before running: `$Env:TELEGRAM_BOT_TOKEN = 'your-token'" -ForegroundColor Yellow
    exit 1
}

# --- Helpers ------------------------------------------------------------------

function Start-AJAJob {
    param([string]$Name, [string]$Module)
    $job = Start-Job -Name $Name -ScriptBlock {
        param($root, $mod)
        Set-Location $root
        python -m $mod
    } -ArgumentList $Root, $Module
    Write-Host "[+] Started $Name (Job ID: $($job.Id))" -ForegroundColor Green
    return $job
}

# --- Launch both components ---------------------------------------------------

$GatewayJob = Start-AJAJob -Name "AJAGateway" -Module "aja.gateway.server"
$WorkerJob  = Start-AJAJob -Name "AJAWorker"  -Module "aja.runtime.autonomous_loop"

$GatewayRestarts = 0
$WorkerRestarts  = 0

Write-Host ""
Write-Host "AJA is LIVE. Monitoring every $PollIntervalSeconds s." -ForegroundColor Green
Write-Host "  Stop-Job * | Remove-Job *  to shut down." -ForegroundColor DarkGray
Write-Host "--------------------------------------------------"

# --- Supervised monitor loop --------------------------------------------------

while ($true) {
    Start-Sleep -Seconds $PollIntervalSeconds

    # Gateway
    $gw = Get-Job -Name "AJAGateway" -ErrorAction SilentlyContinue
    if (-not $gw -or $gw.State -ne 'Running') {
        if ($MaxRestarts -gt 0 -and $GatewayRestarts -ge $MaxRestarts) {
            Write-Host "[!] AJAGateway exceeded $MaxRestarts restarts — giving up." -ForegroundColor Red
        } else {
            Write-Host "[!] AJAGateway stopped (restarts: $GatewayRestarts). Restarting..." -ForegroundColor Yellow
            if ($gw) { Remove-Job $gw -Force }
            $GatewayJob = Start-AJAJob -Name "AJAGateway" -Module "aja.gateway.server"
            $GatewayRestarts++
        }
    }

    # Worker
    $wk = Get-Job -Name "AJAWorker" -ErrorAction SilentlyContinue
    if (-not $wk -or $wk.State -ne 'Running') {
        if ($MaxRestarts -gt 0 -and $WorkerRestarts -ge $MaxRestarts) {
            Write-Host "[!] AJAWorker exceeded $MaxRestarts restarts — giving up." -ForegroundColor Red
        } else {
            Write-Host "[!] AJAWorker stopped (restarts: $WorkerRestarts). Restarting..." -ForegroundColor Yellow
            if ($wk) { Remove-Job $wk -Force }
            $WorkerJob = Start-AJAJob -Name "AJAWorker" -Module "aja.runtime.autonomous_loop"
            $WorkerRestarts++
        }
    }
}
