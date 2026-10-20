$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\P실무\모델개발'
$queueStatePath = 'D:\P실무\모델개발\artifacts\followup_v4_20261020\queue_state.json'
function Save-QueueState([string]$phase, [string]$detail) {
    [ordered]@{
        pid = $PID
        phase = $phase
        detail = $detail
        updated_at_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $queueStatePath -Encoding utf8
}
try {
    Save-QueueState 'running' 'Resume registered comparisons, preserving successful result JSONs.'
    & '.\experiments\20261020_후속실험_v4\run_priority.ps1'
    Save-QueueState 'completed' 'Comparison queue and saved result checks finished; inspect completion audit.'
    exit 0
} catch {
    Save-QueueState 'failed' $_.Exception.Message
    Write-Error $_
    exit 1
}
