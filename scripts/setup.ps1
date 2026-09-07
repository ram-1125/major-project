$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$frontendPath = Join-Path $projectRoot "frontend"

Write-Host "Setting up the SmartOps Python environment..."
if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv $venvPath
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e "${projectRoot}[dev]"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Python setup is complete, but npm was not found." -ForegroundColor Yellow
    Write-Host "Install the current Node.js LTS release, restart VS Code, and run this script again."
    exit 1
}

Write-Host "Installing dashboard packages..."
Push-Location $frontendPath
try {
    npm install
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "SmartOps setup is complete." -ForegroundColor Green
