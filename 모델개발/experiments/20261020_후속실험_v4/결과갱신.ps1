$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\P실무\모델개발'
$python = 'D:\P실무\모델개발\.venv\Scripts\python.exe'
$steps = @('checks','analysis','error_analysis','uncertainty','calibration_analysis','report','brief','checkpoint','completion_review')
foreach ($step in $steps) {
    if ($step -eq 'analysis') {
        & $python -X utf8 -m bike_demo.followup_v4.analysis --phase evaluate
    } else {
        & $python -X utf8 -m ('bike_demo.followup_v4.' + $step)
    }
    if ($LASTEXITCODE -ne 0) { throw ('Saved result refresh failed: ' + $step) }
}
