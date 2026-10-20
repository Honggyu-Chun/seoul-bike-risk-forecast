$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\P실무\모델개발'
$existingTraining = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'bike_demo\.followup_v4\.(runner|fast_runner|advanced|neural|air|confirm)'
})
if ($existingTraining.Count -gt 0) { throw 'A followup training process already exists; inspect it before starting another queue.' }
$env:OMP_NUM_THREADS = '4'
$env:OPENBLAS_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
$env:PYTHONUNBUFFERED = '1'
$python = 'D:\P실무\모델개발\.venv\Scripts\python.exe'
& $python -X utf8 -m bike_demo.followup_v4.fast_runner --folds A B C --names persistence seasonal_day seasonal_week smoothed_markov logistic random_forest lightgbm_basic
if ($LASTEXITCODE -ne 0) { throw 'Baseline comparison failed.' }
& $python -X utf8 -m bike_demo.followup_v4.fast_runner --folds A B C --names lightgbm_full catboost_temporal xgboost_temporal
if ($LASTEXITCODE -ne 0) { throw 'Strong-control comparison failed; inspect log before resume.' }
& $python -X utf8 -m bike_demo.followup_v4.advanced --folds A B C --names lightgbm_temporal --blends
if ($LASTEXITCODE -ne 0) { throw 'Matched temporal control or ensemble failed.' }
& $python -X utf8 -m bike_demo.followup_v4.fast_runner --folds A --names lightgbm_full_112d lightgbm_full_recency28 lightgbm_full_63 lightgbm_ordinal lightgbm_poisson lightgbm_state_experts
if ($LASTEXITCODE -ne 0) { throw 'Development ablation failed.' }
& $python -X utf8 -m bike_demo.followup_v4.advanced --folds A --names lightgbm_calendar lightgbm_prior_year lightgbm_kbo
if ($LASTEXITCODE -ne 0) { throw 'Public-data ablation failed.' }
& $python -X utf8 -m bike_demo.followup_v4.air --folds A --names lightgbm_air_lag2 lightgbm_air_lag3
if ($LASTEXITCODE -ne 0) { throw 'Live-source-compatible air ablation failed.' }
& $python -X utf8 -m bike_demo.followup_v4.neural --folds A
if ($LASTEXITCODE -ne 0) { throw 'Temporal/graph pilot failed.' }
& $python -X utf8 -m bike_demo.followup_v4.confirm
if ($LASTEXITCODE -ne 0) { throw 'Development confirmation failed.' }
& $python -X utf8 -m bike_demo.followup_v4.checks
if ($LASTEXITCODE -ne 0) { throw 'Model roundtrip verification failed.' }
& $python -X utf8 -m bike_demo.followup_v4.analysis --phase evaluate
if ($LASTEXITCODE -ne 0) { throw 'Evaluation export failed.' }
& $python -X utf8 -m bike_demo.followup_v4.error_analysis
if ($LASTEXITCODE -ne 0) { throw 'Subgroup error analysis failed.' }
& $python -X utf8 -m bike_demo.followup_v4.uncertainty
if ($LASTEXITCODE -ne 0) { throw 'Paired day-block sensitivity export failed.' }
& $python -X utf8 -m bike_demo.followup_v4.report
if ($LASTEXITCODE -ne 0) { throw 'Technical report failed.' }
& $python -X utf8 -m bike_demo.followup_v4.brief
if ($LASTEXITCODE -ne 0) { throw 'Readable report failed.' }
& $python -X utf8 -m bike_demo.followup_v4.checkpoint
if ($LASTEXITCODE -ne 0) { throw 'Checkpoint failed.' }
