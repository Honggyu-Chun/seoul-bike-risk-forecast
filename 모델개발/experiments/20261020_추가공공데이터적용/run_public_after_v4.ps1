param([int]$DependencyPid)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\P실무\모델개발'
$python = 'D:\P실무\모델개발\.venv\Scripts\python.exe'
$record = 'D:\P실무\모델개발\experiments\20261020_추가공공데이터적용'
$output = 'D:\P실무\모델개발\artifacts\followup_public_20261020'
New-Item -ItemType Directory -Path $output -Force | Out-Null
function Save-PublicState([string]$phase, [string]$detail) {
    [ordered]@{ pid=$PID; phase=$phase; detail=$detail; dependency_pid=$DependencyPid;
        updated_at_utc=[DateTime]::UtcNow.ToString('o') } | ConvertTo-Json |
        Set-Content -LiteralPath "$output\queue_state.json" -Encoding utf8
}
function Invoke-Checked([string]$module, [string[]]$arguments=@()) {
    & $python -X utf8 -m $module @arguments
    if ($LASTEXITCODE -ne 0) { throw ('Public comparison step failed: '+$module) }
}
try {
    Save-PublicState 'waiting_for_v4' 'Wait for the actual existing v4 launcher to terminate; do not start parallel fitting.'
    if ($DependencyPid -gt 0) {
        while (Get-Process -Id $DependencyPid -ErrorAction SilentlyContinue) {
            Start-Sleep -Seconds 15
        }
    }
    Save-PublicState 'verify_v4' 'Refresh completed v4 predictions, comparisons and evidence before extension fitting.'
    & '.\experiments\20261020_후속실험_v4\결과갱신.ps1'
    Save-PublicState 'fit_public' 'Fit the nine registered bus/OD ablations sequentially with model reload checks.'
    Invoke-Checked 'bike_demo.followup_v4.public_runner'
    Save-PublicState 'report_public' 'Compare all public-data results with frozen controls and reconstruct exact decisions.'
    $env:OMP_NUM_THREADS='1'
    $env:OPENBLAS_NUM_THREADS='1'
    $env:MKL_NUM_THREADS='1'
    Invoke-Checked 'bike_demo.followup_v4.public_report' @('--require-complete')
    & $python -X utf8 "$record\검수와보고서_갱신.py"
    if ($LASTEXITCODE -ne 0) { throw 'Public data explanation refresh failed' }
    & $python -X utf8 "$record\보존과체크포인트.py"
    if ($LASTEXITCODE -ne 0) { throw 'Public input/result snapshot failed' }
    Invoke-Checked 'bike_demo.followup_v4.brief'
    Invoke-Checked 'bike_demo.followup_v4.checkpoint'
    Invoke-Checked 'bike_demo.followup_v4.completion_review'
    Save-PublicState 'completed_pending_manual_review' 'Nine fits and automated checks finished; complete final manual report and goal audit.'
    exit 0
} catch {
    Save-PublicState 'failed' $_.Exception.Message
    Write-Error $_
    exit 1
}
