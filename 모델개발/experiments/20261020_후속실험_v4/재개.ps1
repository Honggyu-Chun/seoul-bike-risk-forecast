$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\P실무\모델개발'
$runningJobs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'bike_demo\.followup_v4\.(runner|fast_runner|advanced|neural|air|confirm)'
})
if ($runningJobs.Count -gt 0) {
    throw '이미 후속 모델 학습이 실행 중이다. 중복 실행하지 않고 기존 작업 상태를 먼저 확인한다.'
}
$python = 'D:\P실무\모델개발\.venv\Scripts\python.exe'
$env:OMP_NUM_THREADS = '4'
$env:OPENBLAS_NUM_THREADS = '4'
$env:PYTHONUNBUFFERED = '1'
& $python -X utf8 -m bike_demo.followup_v4.analysis --phase prepare
if ($LASTEXITCODE -ne 0) { throw 'E2E 준비 분석에 실패했다.' }
& '.\experiments\20261020_후속실험_v4\run_priority.ps1'
if ($LASTEXITCODE -ne 0) { throw '모델 비교가 완료되지 않았다. 성공 JSON은 보존하고 오류부터 확인한다.' }
Write-Output '등록한 비교 큐와 결과 검수를 완료했다. 쉬운보고서와 체크포인트를 확인하고 최종 완료 여부를 검토한다.'
