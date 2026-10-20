@echo off
setlocal
cd /d "%~dp0"
echo CrossMatch admin recovery - NO HOST PYTHON required.
echo Use in the EXISTING deployment folder containing database\crossmatch_web_v631.db.
if not exist "database\crossmatch_web_v631.db" goto no_database
where docker >nul 2>&1
if errorlevel 1 goto no_docker
docker info >nul 2>&1
if errorlevel 1 goto no_docker
docker compose build web
if errorlevel 1 goto failed
echo Resetting an existing admin with the Python interpreter INSIDE the container.
docker compose run --rm --no-deps --entrypoint python web reset_admin.py
if errorlevel 1 goto failed
echo Save the newly displayed username and temporary password securely.
pause
exit /b 0
:no_database
echo No EXISTING database found. First-time installation: open /setup.
goto failed
:no_docker
echo Start Docker Desktop first; host Python is not required.
:failed
echo No account database was deleted by this script.
pause
exit /b 1
