$ErrorActionPreference = "Continue"
$scriptDir = $PSScriptRoot
$pythonExe  = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) { $pythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
$guiMain    = "$scriptDir\study-gui\main.py"

Write-Host "=== Study Launcher ===" -ForegroundColor Cyan

if (Test-Path $pythonExe) {
    Write-Host "[gui] starting..." -ForegroundColor Green
    Start-Process -FilePath $pythonExe -ArgumentList "`"$guiMain`"" -WorkingDirectory $scriptDir
} else {
    Write-Host "[gui] python.exe not found at $pythonExe" -ForegroundColor Red
}

Write-Host "=== Done ===" -ForegroundColor Cyan
Start-Sleep 2
