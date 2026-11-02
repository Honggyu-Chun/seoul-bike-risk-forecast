@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0모델개발"
if not exist ".venv-contract\Scripts\python.exe" (
  echo Run SETUP_CYCLEON_MOCK.cmd first.
  exit /b 1
)
set "CYCLEON_MOCK_TOKEN=local-mock-only-change-this-token-123456"
echo Synthetic local mock only. Docs: http://127.0.0.1:18011/docs
".venv-contract\Scripts\python.exe" scripts\cycleon_mock_service.py ai %*
