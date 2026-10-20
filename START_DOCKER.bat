@echo off
setlocal
cd /d "%~dp0"
where docker >nul 2>&1
if errorlevel 1 goto no_docker
docker info >nul 2>&1
if errorlevel 1 goto no_docker
docker compose version >nul 2>&1
if errorlevel 1 goto no_docker
rem New installation: prepare a strong local setup key before starting the container.
rem Existing deployments keep their database and do not need a new setup key.
if not exist "database\crossmatch_web_v631.db" (
    call GENERATE_SETUP_KEY.bat
    if errorlevel 1 goto failed_setup_key
)
echo Starting CrossMatch web application. Python is packaged INSIDE the image, NOT installed on this laptop.
echo For first build, an internet connection may be needed to download Docker packages.
docker compose up -d --build --force-recreate --wait --wait-timeout 240
if errorlevel 1 goto failed
echo Verifying the running CrossMatch application version:
docker compose exec -T web python -c "from config import APP_VERSION; import sys; print('CrossMatch running version:', APP_VERSION); sys.exit(0 if APP_VERSION == '6.32.64' else 1)"
if errorlevel 1 goto failed
call OPEN_APP.bat
exit /b 0
:failed_setup_key
echo Setup key generation failed. No administrator setup was unlocked.
echo Run GENERATE_SETUP_KEY.bat on this computer and review any error above.
pause
exit /b 1
:no_docker
echo Please install/start Docker Desktop in LINUX CONTAINERS mode on Windows 11.
echo No Python installation is necessary on your laptop.
pause
exit /b 1
:failed
echo Startup failed. Latest service logs:
docker compose logs --tail 60
echo Ensure ports in .env are free.
pause
exit /b 1
