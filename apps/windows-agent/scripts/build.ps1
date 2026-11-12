# Build dist\MindLoom\MindLoom.exe with PyInstaller.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-WebBase https://app.example.com] [-ApiBase /api]
#
# -WebBase writes dist\MindLoom\install.json next to the exe, so the app signs in to that
# site (see mindloom_agent/config.py). Without it the app uses http://localhost:5500.
param([string]$WebBase = "", [string]$ApiBase = "/api")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }

# The process must be called MindLoom: the app refuses to operate itself by that name.
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name MindLoom `
    --collect-submodules comtypes --hidden-import comtypes.stream scripts\entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

if ($WebBase) {
    @{ webBase = $WebBase; apiBase = $ApiBase } | ConvertTo-Json | Set-Content -Encoding UTF8 "dist\MindLoom\install.json"
}
Write-Host "Built dist\MindLoom\MindLoom.exe"
