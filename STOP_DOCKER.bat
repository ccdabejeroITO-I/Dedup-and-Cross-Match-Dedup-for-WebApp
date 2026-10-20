@echo off
setlocal
cd /d "%~dp0"
echo Stop CrossMatch only. Uploaded files, results and accounts are retained.
echo Wait for processing to finish before stopping; running jobs may be interrupted.
choice /C YN /N /M "Stop CrossMatch now? [Y/N] "
if errorlevel 2 exit /b 0
docker compose stop
if errorlevel 1 pause
