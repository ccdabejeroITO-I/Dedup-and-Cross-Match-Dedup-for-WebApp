@echo off
setlocal
cd /d "%~dp0"
set "CROSSMATCH_HTTP_PORT=18090"
set "CROSSMATCH_HTTPS_PORT=18444"
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
  if "%%A"=="CROSSMATCH_HTTP_PORT" set "CROSSMATCH_HTTP_PORT=%%B"
  if "%%A"=="CROSSMATCH_HTTPS_PORT" set "CROSSMATCH_HTTPS_PORT=%%B"
)
powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$env:CROSSMATCH_HTTP_PORT+'/api/selftest') -TimeoutSec 5; if (-not $r.ok -or $r.engine -eq 'unknown') { exit 1 }; exit 0 } catch { exit 1 }"
if errorlevel 1 goto not_running
powershell -NoProfile -Command "[Net.ServicePointManager]::ServerCertificateValidationCallback = {$true}; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { $r=Invoke-RestMethod -Uri ('https://localhost:'+$env:CROSSMATCH_HTTPS_PORT+'/api/selftest') -TimeoutSec 5; if ($r.ok -and $r.engine -ne 'unknown') { exit 0 }; exit 1 } catch { exit 1 }"
if errorlevel 1 goto http
start "CrossMatch" "https://localhost:%CROSSMATCH_HTTPS_PORT%"
exit /b 0
:http
start "CrossMatch" "http://127.0.0.1:%CROSSMATCH_HTTP_PORT%"
exit /b 0
:not_running
echo CrossMatch is not ready. Run START_DOCKER.bat.
pause
exit /b 1
