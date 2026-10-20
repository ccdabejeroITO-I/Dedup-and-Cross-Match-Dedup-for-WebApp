@echo off
setlocal
cd /d "%~dp0"
where powershell >nul 2>&1
if errorlevel 1 (
  echo Windows PowerShell is required to generate the setup key.
  exit /b 1
)
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0GENERATE_SETUP_KEY.ps1"
if errorlevel 1 exit /b 1
exit /b 0
