# Run script for Browser-Use Agent Integration project
# This script starts all components: Python agent server, Trigger.dev task, and opens the frontend

Write-Host "Starting Browser-Use Agent Integration..."

# Function to start a process in background
function Start-BackgroundProcess {
    param([string]$command, [string]$workingDir)
    $job = Start-Job -ScriptBlock {
        param($cmd, $dir)
        Set-Location $dir
        Invoke-Expression $cmd
    } -ArgumentList $command, $workingDir

    return $job
}

# Ensure Python venv is present and runnable. This repo can be shared/zipped and
# a committed venv may contain a python shim pointing to a user-specific path.
function Ensure-AgentVenv {
    param(
        [string]$repoDir,
        [string]$agentDir
    )

    $venvDir = Join-Path $agentDir ".venv"
    $pythonExe = Join-Path $venvDir "Scripts\python.exe"
    $requirements = Join-Path $agentDir "requirements.txt"

    $venvOk = $false
    if (Test-Path $pythonExe) {
        try {
            & $pythonExe -c "import sys; print(sys.executable)" | Out-Null
            if ($LASTEXITCODE -eq 0) { $venvOk = $true }
        } catch {
            $venvOk = $false
        }
    }

    if (-not $venvOk) {
        Write-Host "Python venv missing/broken. Recreating in agent\.venv ..."
        if (Test-Path $venvDir) { Remove-Item -Recurse -Force $venvDir }

        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.12 -m venv $venvDir
        } else {
            & python -m venv $venvDir
        }

        & $pythonExe -m pip install --upgrade pip
        & (Join-Path $venvDir "Scripts\pip.exe") install -r $requirements
    }

    return $pythonExe
}

# 1. Start Python Agent Server
Write-Host "Starting Python Agent Server..."
$repoDir = $PSScriptRoot
$agentDir = Join-Path $repoDir "agent"
$pythonExe = Ensure-AgentVenv -repoDir $repoDir -agentDir $agentDir
$pythonCommand = "& `"$pythonExe`" -m uvicorn agent.server:app --reload --host 0.0.0.0 --port 8002"
$fullPythonCommand = $pythonCommand
$pythonJob = Start-BackgroundProcess -command $fullPythonCommand -workingDir $repoDir

# Wait a bit for server to start
Start-Sleep -Seconds 5

# 2. Start Trigger.dev Task
Write-Host "Starting Trigger.dev Task..."
$triggerDir = Join-Path $PSScriptRoot "trigger"
$triggerCommand = "npm run dev"
$triggerJob = Start-BackgroundProcess -command $triggerCommand -workingDir $triggerDir

# Wait a bit for trigger to start
Start-Sleep -Seconds 5

# 3. Open Frontend in browser
Write-Host "Opening Frontend..."
$frontendUrl = "http://127.0.0.1:8002/"
Start-Process $frontendUrl

Write-Host "All components started!"
Write-Host "Python Agent: http://localhost:8002"
Write-Host "Trigger.dev: Check console for URL"
Write-Host "Frontend: http://127.0.0.1:8002/"
Write-Host "Press Ctrl+C to stop all services"

# Keep script running to monitor jobs
try {
    while ($true) {
        Start-Sleep -Seconds 10
        # Check if jobs are still running
        $pythonJob | Receive-Job
        $triggerJob | Receive-Job
    }
} finally {
    Write-Host "Stopping services..."
    $pythonJob | Stop-Job
    $triggerJob | Stop-Job
    $pythonJob | Remove-Job
    $triggerJob | Remove-Job
}
