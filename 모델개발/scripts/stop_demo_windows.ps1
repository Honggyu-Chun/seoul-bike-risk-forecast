param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $demoRoot "reports\local_runs\windows_20261020\server-$Port.json"
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No locally managed server record.'; exit 0 }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$demoProcessId = [int]$state.processId
$running = Get-CimInstance Win32_Process -Filter "ProcessId = $demoProcessId"
if ($null -eq $running) { Write-Output 'Server is already stopped.'; exit 0 }
if ($running.ExecutablePath -ne $state.executable -or $running.CommandLine -notlike '*uvicorn*bike_demo.api:app*') { throw 'The saved PID belongs to another process. Nothing was stopped.' }
$children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $demoProcessId" | Where-Object { $_.CommandLine -like '*uvicorn*bike_demo.api:app*' }
foreach ($child in $children) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
Stop-Process -Id $demoProcessId -ErrorAction SilentlyContinue
Write-Output "Stopped local demo on port $Port."
