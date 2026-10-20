@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0모델개발\scripts\start_demo_windows.ps1" -OpenBrowser
if errorlevel 1 pause
