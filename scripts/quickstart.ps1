# =============================================================================
# AJA Interactive Quickstart (Windows PowerShell)
# Clone -> venv -> install -> configure Telegram -> doctor -> chat.
# Idempotent: safe to re-run; existing artifacts are detected and reused.
# =============================================================================
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $RepoRoot '.venv'
$EnvFile = Join-Path $RepoRoot '.env'

function Write-Ok   { param($m) Write-Host "[OK] "  -NoNewline -ForegroundColor Green;   Write-Host $m }
function Write-Step { param($m) Write-Host "[->] " -NoNewline -ForegroundColor Cyan;    Write-Host $m }
function Write-Warn { param($m) Write-Host "[!] "  -NoNewline -ForegroundColor Yellow;  Write-Host $m }
function Die        { param($m)
    Write-Warn $m
    Write-Host "    Common fixes:"
    Write-Host "    - Delete .venv\ and re-run to rebuild the environment."
    Write-Host "    - Ensure network access for pip."
    Write-Host "    - Run 'python -m aja doctor' for diagnostics."
    exit 1
}

Write-Host ""
Write-Host "============ AJA QUICKSTART ============" -ForegroundColor Magenta
Write-Host ""

# -- a) Python >= 3.11 (prefer the py launcher pinned to 3.12) ----------------
Write-Step "Checking Python version..."
$PyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        py -3.12 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $PyCmd = @('py', '-3.12') }
    } catch { }
}
if (-not $PyCmd -and (Get-Command python -ErrorAction SilentlyContinue)) { $PyCmd = @('python') }
if (-not $PyCmd) {
    Die "Python not found. Install Python 3.11+ from https://www.python.org/downloads/"
}
$verOutput = & $PyCmd[0] $PyCmd[1] -c "import sys; print('%d.%d' % (sys.version_info.major, sys.version_info.minor))"
$pyOk = & $PyCmd[0] $PyCmd[1] -c "import sys; print(1 if sys.version_info >= (3, 11) else 0)"
if ("$pyOk".Trim() -ne '1') {
    Die "Python $verOutput found, but 3.11+ is required. Install from https://www.python.org/downloads/"
}
Write-Ok "Python $verOutput"

# -- b) git available ----------------------------------------------------------
Write-Step "Checking git..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git not found. Install git: https://git-scm.com/downloads"
}
Write-Ok "git available"

Set-Location $RepoRoot

# -- c) Create/reuse .venv -----------------------------------------------------
$ReuseVenv = $false
if (Test-Path $VenvDir) {
    if (Test-Path (Join-Path $VenvDir 'Scripts\python.exe')) {
        $answer = Read-Host "[?] Existing .venv found. Reuse it? [Y/n]"
        if ($answer -match '^[nN]') {
            Write-Step "Recreating .venv..."
            Remove-Item -Recurse -Force $VenvDir
            & $PyCmd[0] $PyCmd[1] -m venv $VenvDir
        } else {
            $ReuseVenv = $true
        }
    } else {
        Write-Warn "Existing .venv lacks Scripts\python.exe -- recreating."
        Remove-Item -Recurse -Force $VenvDir
        & $PyCmd[0] $PyCmd[1] -m venv $VenvDir
    }
} else {
    Write-Step "Creating virtual environment (.venv)..."
    & $PyCmd[0] $PyCmd[1] -m venv $VenvDir
}
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
& $VenvPython -c "import sys"
if ($LASTEXITCODE -ne 0) { Die ".venv python is broken. Delete .venv\ and re-run." }
Write-Ok "Virtual environment ready ($(& $VenvPython --version))"

# -- d) pip install -e ".[telegram]" --------------------------------------------
$SkipInstall = $false
if ($ReuseVenv) {
    $answer = Read-Host "[?] Reinstall package (pip install -e)? [y/N]"
    if ($answer -notmatch '^[yY]') {
        Write-Ok "Skipping installation (reusing existing)."
        $SkipInstall = $true
    }
}
if (-not $SkipInstall) {
    Write-Step "Installing AJA (editable, telegram extras) -- this can take a few minutes..."
    & $VenvPython -m pip install --upgrade pip | Out-Null
    & $VenvPython -m pip install -e ".[telegram]"
    if ($LASTEXITCODE -ne 0) { Die "pip install failed. Check output above." }
    Write-Ok "AJA installed"
}

# -- e) .env: reuse or create from template --------------------------------------
$CreateEnv = $true
if (Test-Path $EnvFile) {
    $answer = Read-Host "[?] Existing .env found. Reuse it? [Y/n]"
    if ($answer -match '^[nN]') {
        Copy-Item $EnvFile "$EnvFile.bak.$([DateTimeOffset]::Now.ToUnixTimeSeconds())"
        Write-Step "Backed up old .env; creating a fresh one."
    } else {
        $CreateEnv = $false
        Write-Ok "Reusing existing .env"
    }
}
if ($CreateEnv) {
    Write-Step "Creating .env from template..."
    $template = @'
# ==============================================================
# AJA local environment (Telegram-first quickstart)
# NEVER commit this file.
# ==============================================================

# REQUIRED - bot token from @BotFather on Telegram
TELEGRAM_BOT_TOKEN=

# REQUIRED - your numeric Telegram user id(s) from @userinfobot.
# Fail-safe: without an allowlist, every remote user is DENIED.
TELEGRAM_ALLOWED_USER_IDS=

# OPTIONAL - GitHub Copilot token as LLM provider (gh CLI token works).
# COPILOT_GITHUB_TOKEN=

# OPTIONAL - shared HMAC secret for fleet/baton transfers between hosts.
# Generate: python -c "import secrets; print(secrets.token_hex(32))"
# AJA_BATON_SECRET=

# OPTIONAL - embeddings backend (auto/onnx/mock). Leave unset for auto.
# AJA_EMBEDDING_BACKEND=

# OPTIONAL - scheduled-mission and worker timeouts (seconds).
# AJA_JOB_TIMEOUT_S=600
# AJA_WORKER_TIMEOUT_S=600
# AJA_WORKER_RUN_TESTS=0

# OPTIONAL - Google Calendar integration (see docs/operator/CALENDAR.md).
# GOOGLE_CALENDAR_CLIENT_SECRET=
'@
    Set-Content -Path $EnvFile -Value $template -Encoding ASCII
    Write-Ok ".env created"
}

# -- f/g) Prompt credentials (skip when reusing a filled .env) --------------------
$PromptCreds = $true
if (-not $CreateEnv) {
    $lines = Get-Content $EnvFile
    $hasToken = @($lines | Where-Object { $_ -match '^TELEGRAM_BOT_TOKEN=.+' }).Count -gt 0
    $hasIds   = @($lines | Where-Object { $_ -match '^TELEGRAM_ALLOWED_USER_IDS=.+' }).Count -gt 0
    if ($hasToken -and $hasIds) {
        $PromptCreds = $false
        Write-Ok "Telegram credentials already configured in .env"
    }
}

if ($PromptCreds) {
    Write-Host ""
    Write-Host "-------------- Telegram configuration ---------------"
    $botToken = ''
    while ($true) {
        $botToken = Read-Host "[?] Paste TELEGRAM_BOT_TOKEN (from @BotFather)"
        if ([string]::IsNullOrWhiteSpace($botToken)) {
            Write-Warn "Token cannot be empty."
        } elseif ($botToken -notmatch '^\d+:[A-Za-z0-9_-]+$') {
            Write-Warn "That does not look like a bot token (expected format digits_colon_alphanumeric). Try again."
        } else {
            break
        }
    }
    $userIds = ''
    while ([string]::IsNullOrWhiteSpace($userIds)) {
        Write-Host "    Get YOUR numeric user id by messaging @userinfobot on Telegram."
        $userIds = Read-Host "[?] Paste TELEGRAM_ALLOWED_USER_IDS (comma-separated ids)"
        if ([string]::IsNullOrWhiteSpace($userIds)) {
            Write-Warn "At least one id is REQUIRED -- the gateway denies everyone otherwise."
        }
    }
    $content = @(Get-Content $EnvFile | Where-Object { $_ -notmatch '^(TELEGRAM_BOT_TOKEN|TELEGRAM_ALLOWED_USER_IDS)=' })
    $content += "TELEGRAM_BOT_TOKEN=$botToken"
    $content += "TELEGRAM_ALLOWED_USER_IDS=$userIds"
    Set-Content -Path $EnvFile -Value $content -Encoding ASCII
    Write-Ok "Credentials written to .env"
}

# -- h) doctor gate ----------------------------------------------------------------
Write-Step "Running diagnostics (aja doctor)..."
& $VenvPython -m aja doctor
if ($LASTEXITCODE -ne 0) {
    Die "Doctor reported problems. Fix the issues above, then re-run this script."
}
Write-Ok "Doctor passed"

# -- i) Success box ------------------------------------------------------------------
Write-Host ""
Write-Host "+------------------------------------------------------+" -ForegroundColor Green
Write-Host "|           AJA IS READY - LET'S CHAT                  |" -ForegroundColor Green
Write-Host "+------------------------------------------------------+" -ForegroundColor Green
Write-Host "|  Next steps:                                         |"
Write-Host "|                                                      |"
Write-Host "|   1. Start everything:        aja serve              |"
Write-Host "|   2. Open Telegram and send                          |"
Write-Host "|      a message to your bot.                          |"
Write-Host "|                                                      |"
Write-Host "|  Useful commands:                                    |"
Write-Host "|    aja doctor     - validate setup                   |"
Write-Host "|    aja ws         - manage workspaces                |"
Write-Host '|    aja run "..."   - run a mission locally           |'
Write-Host "+------------------------------------------------------+" -ForegroundColor Green
