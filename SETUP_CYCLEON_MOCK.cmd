@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0모델개발"
if defined CYCLEON_PYTHON goto explicit_python
where py >nul 2>&1
if errorlevel 1 goto path_python
py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 goto path_python
py -3.12 scripts\cycleon_setup.py
goto setup_done
:path_python
python scripts\cycleon_setup.py
goto setup_done
:explicit_python
"%CYCLEON_PYTHON%" scripts\cycleon_setup.py
:setup_done
if errorlevel 1 exit /b 1
echo Setup completed. Run RUN_CYCLEON_MOCK.cmd next.
