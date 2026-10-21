@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "P_PROJECT_PROFILE=%~1"
if "%P_PROJECT_PROFILE%"=="" set "P_PROJECT_PROFILE=demo"
if /I not "%P_PROJECT_PROFILE%"=="demo" if /I not "%P_PROJECT_PROFILE%"=="research" (
    echo Usage: SETUP_P_PROJECT.cmd [demo^|research]
    exit /b 1
)
cd /d "%~dp0모델개발"
set "P_PROJECT_UV=uv"
where uv >nul 2>&1
if errorlevel 1 (
    if exist "%~dp0작업기록\문서작성및초기설정\local-transfer-20261020\tools\bin\uv.exe" (
        set "P_PROJECT_UV=%~dp0작업기록\문서작성및초기설정\local-transfer-20261020\tools\bin\uv.exe"
    ) else (
        echo Install uv first: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
)
if /I "%P_PROJECT_PROFILE%"=="research" (
    "%P_PROJECT_UV%" sync --frozen --inexact --group research --system-certs --link-mode copy --python 3.12.15
) else (
    "%P_PROJECT_UV%" sync --frozen --inexact --system-certs --link-mode copy --python 3.12.15
)
if errorlevel 1 goto failed
"%P_PROJECT_UV%" run --frozen --no-sync python scripts/setup_runtime.py --profile %P_PROJECT_PROFILE%
if errorlevel 1 goto failed
if not exist .env copy .env.example .env >nul
"%P_PROJECT_UV%" run --frozen --no-sync python scripts/check_runtime.py
if errorlevel 1 goto failed
echo Ready. Run START_P_PROJECT.cmd to open the demo.
exit /b 0
:failed
echo Setup failed. Check the message above and docs/TEAM_SETUP.md.
pause
exit /b 1
