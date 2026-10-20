@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
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
"%P_PROJECT_UV%" sync --frozen --group research --system-certs --python 3.12
if errorlevel 1 pause
