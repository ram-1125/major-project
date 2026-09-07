$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$frontendPath = Join-Path $projectRoot "frontend"
$viteEntry = Join-Path $frontendPath "node_modules\vite\bin\vite.js"
$displayInterval = if ($env:SMARTOPS_INTERVAL_SECONDS) {
    $env:SMARTOPS_INTERVAL_SECONDS
}
else {
    "30"
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "SmartOps is not set up. Run .\scripts\setup.ps1 first."
}
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    throw "Node.js is unavailable. Install Node.js LTS and restart VS Code."
}
if (-not (Test-Path -LiteralPath $viteEntry)) {
    throw "Frontend packages are missing. Run .\scripts\setup.ps1 first."
}

$startedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Stop-SmartOpsProcessTree {
    param([int]$RootProcessId)

    $children = @(
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootProcessId" `
            -ErrorAction SilentlyContinue
    )
    foreach ($child in $children) {
        Stop-SmartOpsProcessTree -RootProcessId $child.ProcessId
    }
    Stop-Process -Id $RootProcessId -Force -ErrorAction SilentlyContinue
}

try {
    $agentProcess = Start-Process `
        -FilePath $venvPython `
        -ArgumentList "-m", "agent.main" `
        -WorkingDirectory $projectRoot `
        -NoNewWindow `
        -PassThru
    $startedProcesses.Add($agentProcess)

    $apiProcess = Start-Process `
        -FilePath $venvPython `
        -ArgumentList "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory $projectRoot `
        -NoNewWindow `
        -PassThru
    $startedProcesses.Add($apiProcess)

    $quotedViteEntry = "`"$viteEntry`""
    $frontendProcess = Start-Process `
        -FilePath $nodeCommand.Source `
        -ArgumentList $quotedViteEntry, "--host", "localhost", "--port", "5173", "--strictPort" `
        -WorkingDirectory $frontendPath `
        -NoNewWindow `
        -PassThru
    $startedProcesses.Add($frontendProcess)

    Start-Sleep -Seconds 1
    $failedProcess = $startedProcesses | Where-Object { $_.HasExited } | Select-Object -First 1
    if ($failedProcess) {
        throw "A SmartOps process exited during startup. Review the messages above."
    }

    Write-Host ""
    Write-Host "SmartOps is running." -ForegroundColor Green
    Write-Host "  Agent PID:    $($agentProcess.Id) - collecting and dispatching eligible local alerts every $displayInterval seconds"
    Write-Host "  API PID:      $($apiProcess.Id) - http://127.0.0.1:8000"
    Write-Host "  Frontend PID: $($frontendProcess.Id) - http://localhost:5173/#/overview"
    Write-Host "  Settings:     http://localhost:5173/#/settings"
    Write-Host "  Signals:      Advanced local Windows signals are collecting for context only"
    Write-Host "  Notifications: selected local Advisory, Warning or Urgent categories (when enabled)"
    Write-Host ""
    Write-Host "Press Ctrl+C here once to stop all three processes."

    while ($true) {
        $exitedProcess = $startedProcesses | Where-Object { $_.HasExited } | Select-Object -First 1
        if ($exitedProcess) {
            # A console Ctrl+C is broadcast to the attached children. They may
            # exit a fraction of a second before PowerShell observes the
            # interrupt, so distinguish coordinated shutdown from one failure.
            Start-Sleep -Milliseconds 750
            $exitedCount = @($startedProcesses | Where-Object { $_.HasExited }).Count
            if ($exitedCount -ge 2) {
                break
            }
            throw "SmartOps process $($exitedProcess.Id) stopped unexpectedly."
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    Write-Host ""
    Write-Host "Stopping SmartOps..."
    # Ctrl+C is also delivered to console-attached child processes. Give the
    # Python agent a brief opportunity to run its own KeyboardInterrupt cleanup
    # and close the durable single-owner session before using the fallback kill.
    foreach ($process in $startedProcesses) {
        if (-not $process.HasExited) {
            $process.WaitForExit(3000) | Out-Null
        }
    }
    foreach ($process in $startedProcesses) {
        Stop-SmartOpsProcessTree -RootProcessId $process.Id
        $process.WaitForExit(3000) | Out-Null
    }
    & $venvPython -m agent.main --cleanup-orphaned-sessions | Out-Host
    Write-Host "SmartOps stopped. No managed processes were left running." -ForegroundColor Green
}
