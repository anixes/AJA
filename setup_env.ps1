# Agent Environment Setup
# Run this script to ensure Python 3.12 is prioritized and Anaconda is ignored.

$env:PYTHONPATH = "$PSScriptRoot;$(Get-Location)"
$env:PYO3_PYTHON = "python.exe"

# Ensure Python 3.12 is in your PATH
# $env:PATH = "C:\Path\To\Python312\;$env:PATH"

Write-Host "--- Agent Environment Locked ---" -ForegroundColor Cyan
Write-Host "Python: $env:PYO3_PYTHON"
Write-Host "Anaconda links bypassed for this session."
