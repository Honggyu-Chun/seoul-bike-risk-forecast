$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\P실무\모델개발'
$legacyPid = 12588
$deadline = (Get-Date).AddHours(4)
while ((Get-Date) -lt $deadline) {
    $process = Get-Process -Id $legacyPid -ErrorAction SilentlyContinue
    if (-not $process) { break }
    try {
        $state = Get-Content -LiteralPath 'artifacts\followup_v4_20261020\worker.json' -Raw | ConvertFrom-Json
    } catch { Start-Sleep -Seconds 2; continue }
    if ($state.pid -eq $legacyPid -and $state.candidate -eq 'lightgbm_weather') {
        $line = (Get-CimInstance Win32_Process -Filter "ProcessId = $legacyPid").CommandLine
        if ($line -like '*bike_demo.followup_v4.runner*') {
            Stop-Process -Id $legacyPid
            'Stopped the unverified archived-weather candidate after preserving completed causal controls.' | Set-Content -LiteralPath 'artifacts\followup_v4_20261020\queue_replanned.txt'
        }
        break
    }
    Start-Sleep -Seconds 2
}
