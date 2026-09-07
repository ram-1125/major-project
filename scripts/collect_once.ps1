$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "SmartOps is not set up. Run .\scripts\setup.ps1 first."
}

Push-Location $projectRoot
try {
    & $venvPython -m agent.main --once
}
finally {
    Pop-Location
}

