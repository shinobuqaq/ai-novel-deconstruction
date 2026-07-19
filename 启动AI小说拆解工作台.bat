@echo off
setlocal
title AI Novel Deconstruction Workbench
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Run scripts\setup.ps1 first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "scripts\workbench_launcher.py"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Workbench stopped with exit code %EXIT_CODE%. Check the log path shown above.
  pause
)
exit /b %EXIT_CODE%
