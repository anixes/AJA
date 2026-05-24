# AJA 24/7 Autonomous System Launcher
# Bridges Telegram Gateway with Terminal Autonomous Worker

$Root = Get-Location
$Env:PYTHONPATH = "$Root\libs\aja-core"

Write-Host "--------------------------------------------------" -ForegroundColor Cyan
Write-Host "   AJA: Autonomous Gateway & Execution Loop" -ForegroundColor Cyan
Write-Host "--------------------------------------------------" -ForegroundColor Cyan

# Check for required environment variables
if (-not $Env:TELEGRAM_BOT_TOKEN) {
    Write-Host "❌ Error: TELEGRAM_BOT_TOKEN not found in environment." -ForegroundColor Red
    exit 1
}

Write-Host "[*] Starting AJA Gateway (Telegram + Mission Hub)..." -ForegroundColor Yellow
$GatewayJob = Start-Job -Name "AJAGateway" -ScriptBlock {
    param($root)
    $Env:PYTHONPATH = "$root\libs\aja-core"
    python -m aja.gateway.server
} -ArgumentList $Root

Write-Host "[*] Starting AJA Worker (Autonomous Execution Loop)..." -ForegroundColor Yellow
$WorkerJob = Start-Job -Name "AJAWorker" -ScriptBlock {
    param($root)
    $Env:PYTHONPATH = "$root\libs\aja-core"
    python -m aja.runtime.autonomous_loop
} -ArgumentList $Root

Write-Host "🚀 AJA is now LIVE 24/7." -ForegroundColor Green
Write-Host " - Gateway Job ID: $($GatewayJob.Id)"
Write-Host " - Worker Job ID:  $($WorkerJob.Id)"
Write-Host "--------------------------------------------------"
Write-Host "Commands:"
Write-Host " - Get-Job | Receive-Job : View logs"
Write-Host " - Stop-Job *            : Shutdown AJA"
Write-Host "--------------------------------------------------"

# Monitor the jobs
while ($true) {
    $running = Get-Job | Where-Object { $_.State -eq 'Running' }
    if ($running.Count -lt 2) {
        Write-Host "⚠️ Warning: One or more AJA components stopped!" -ForegroundColor Red
        Get-Job | Select-Object Name, State, ExitCode
    }
    Start-Sleep -Seconds 30
}
