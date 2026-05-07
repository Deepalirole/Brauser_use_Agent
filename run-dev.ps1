# Run script for backend + frontend only
Write-Host "Starting Backend + Frontend..."

function Start-BackgroundProcess {
    param([string]$command, [string]$workingDir)
    $job = Start-Job -ScriptBlock {
        param($cmd, $dir)
        Set-Location $dir
        Invoke-Expression $cmd
    } -ArgumentList $command, $workingDir
    return $job
}

$repoDir = $PSScriptRoot
$agentDir = Join-Path $repoDir "agent"
$pythonExe = Join-Path $agentDir ".venv\Scripts\python.exe"

if (!(Test-Path $pythonExe)) {
    Write-Error "Python venv not found at $pythonExe. Create it in agent/ first."
    exit 1
}

Write-Host "Starting Python Agent Server..."
$backendCommand = "& `"$pythonExe`" -m uvicorn agent.server:app --reload --host 0.0.0.0 --port 8002"
$backendJob = Start-BackgroundProcess -command $backendCommand -workingDir $repoDir

Start-Sleep -Seconds 4

Write-Host "Opening Frontend..."
Start-Process "http://127.0.0.1:8002/"

Write-Host "Backend: http://127.0.0.1:8002"
Write-Host "Frontend: http://127.0.0.1:8002/"
Write-Host "Press Ctrl+C to stop"

try {
    while ($true) {
        Start-Sleep -Seconds 10
        $backendJob | Receive-Job
    }
} finally {
    Write-Host "Stopping backend..."
    $backendJob | Stop-Job
    $backendJob | Remove-Job
}
