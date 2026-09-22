@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_bootstrap.ps1" -Mode Demo
if errorlevel 1 pause
