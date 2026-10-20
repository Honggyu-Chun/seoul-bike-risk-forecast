$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\P실무\모델개발'
$output = 'D:\P실무\모델개발\artifacts\followup_public_20261020'
$record = 'D:\P실무\모델개발\experiments\20261020_추가공공데이터적용'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$previous = "$output\queue_launch.json"
if (Test-Path -LiteralPath $previous) {
    $old = Get-Content -LiteralPath $previous -Raw | ConvertFrom-Json
    if (Get-Process -Id $old.launcher_pid -ErrorAction SilentlyContinue) {
        Write-Output ('PUBLIC_QUEUE_ALREADY_LIVE '+$old.launcher_pid)
        return
    }
}
$source = Get-Content -LiteralPath 'D:\P실무\모델개발\artifacts\followup_v4_20261020\queue_launch.json' -Raw | ConvertFrom-Json
$dependencyPid = 0
if (Get-Process -Id $source.launcher_pid -ErrorAction SilentlyContinue) { $dependencyPid = [int]$source.launcher_pid }
$shell = 'C:\Users\hongg\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe'
$stamp = [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss')
$stdout = "$output\queue_${stamp}.log"
$stderr = "$output\queue_${stamp}_stderr.log"
$process = Start-Process -FilePath $shell -ArgumentList @('-NoProfile','-File',"$record\run_public_after_v4.ps1",'-DependencyPid',"$dependencyPid") -WorkingDirectory 'D:\P실무\모델개발' -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
[ordered]@{launcher_pid=$process.Id; dependency_pid=$dependencyPid; stdout=$stdout; stderr=$stderr;
    launched_at_utc=[DateTime]::UtcNow.ToString('o'); scope='Registered 9 public-data ablations after existing v4 queue'} |
    ConvertTo-Json | Set-Content -LiteralPath $previous -Encoding utf8
Write-Output ('PUBLIC_QUEUE_STARTED '+$process.Id+' WAITING_FOR '+$dependencyPid)
