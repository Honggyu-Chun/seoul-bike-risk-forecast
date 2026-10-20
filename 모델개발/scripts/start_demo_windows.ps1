param([int]$Port = 8000, [switch]$OpenBrowser, [int]$StartupTimeoutSeconds = 300)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$demoRoot = Split-Path -Parent $PSScriptRoot
$demoPython = Join-Path $demoRoot '.venv\Scripts\python.exe'
$demoRun = Join-Path $demoRoot 'reports\local_runs\windows_20261020'
$demoUrl = "http://127.0.0.1:$Port"
if (-not (Test-Path -LiteralPath $demoPython)) { throw 'Run SETUP_P_PROJECT.cmd before starting the demo.' }
New-Item -ItemType Directory -Path $demoRun -Force | Out-Null
$ready = $false
try {
    $health = Invoke-RestMethod "$demoUrl/api/health" -TimeoutSec 2
    if ($health.name -eq 'Seoul Bike Rider Research Demo' -and $health.status -eq 'ok') { $ready = $true }
} catch { }
if (-not $ready) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $demoProcess = Start-Process -FilePath $demoPython -ArgumentList @('-X','utf8','-m','uvicorn','bike_demo.api:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $demoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $demoRun "server-$Port-$stamp.stdout.log") -RedirectStandardError (Join-Path $demoRun "server-$Port-$stamp.stderr.log")
    @{ processId = $demoProcess.Id; executable = $demoPython; port = $Port; url = $demoUrl; startedAt = (Get-Date).ToString("o") } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $demoRun "server-$Port.json") -Encoding utf8
    $startupDeadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $startupDeadline) {
        Start-Sleep -Milliseconds 500
        if ($demoProcess.HasExited) { throw "Server exited. Check logs in $demoRun" }
        try {
            $health = Invoke-RestMethod "$demoUrl/api/health" -TimeoutSec 2
            if ($health.name -eq 'Seoul Bike Rider Research Demo' -and $health.status -eq 'ok') { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw "Server was not ready. Check logs in $demoRun" }
    @{ processId = $demoProcess.Id; executable = $demoPython; port = $Port; url = $demoUrl; startedAt = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $demoRun "server-$Port.json") -Encoding utf8
}
Write-Output "Ready: $demoUrl"
if ($OpenBrowser) { Start-Process $demoUrl }
