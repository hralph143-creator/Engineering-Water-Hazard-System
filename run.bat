@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1

rem ZhiyuShield launcher. Keep this file ASCII-only for cmd.exe compatibility.
set "ROOT=%~dp0"
cd /d "%ROOT%"
if errorlevel 1 goto :bad_root
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

title Engineering Water Hazard Management System
set "MODE=dev"
if /i "%~1"=="app" set "MODE=app"
if /i "%~1"=="--check" set "MODE=check"

echo.
echo ============================================================
echo   Engineering Water Hazard Management System
echo ============================================================
echo.

if /i "%MODE%"=="app" goto :desktop_app

:development_checks
call :find_python
if errorlevel 1 goto :python_missing
call :find_npm
if errorlevel 1 goto :npm_missing
if not exist "%ROOT%backend\app\main.py" goto :backend_missing
if not exist "%ROOT%frontend\package.json" goto :frontend_missing
if not exist "%ROOT%frontend\node_modules" goto :modules_missing

echo [OK] Project root: %ROOT%
echo [OK] Python: %PYTHON_EXE%
echo [OK] npm: %NPM_EXE%

"%PYTHON_EXE%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 goto :python_packages_missing

if /i not "%MODE%"=="check" (
    echo [1/5] Stopping existing system services...
    call :stop_service 8001 backend
    if errorlevel 1 goto :restart_failed
    call :stop_service 5173 frontend
    if errorlevel 1 goto :restart_failed
)

echo [CHECK] Runtime directories and ports...
"%PYTHON_EXE%" -m backend.app.preflight --ports 8001 5173
if errorlevel 1 goto :preflight_failed

if /i "%MODE%"=="check" (
    call "%NPM_EXE%" --version >nul 2>&1
    if errorlevel 1 goto :npm_broken
    echo [OK] Launcher self-check passed.
    exit /b 0
)

echo.
echo [2/5] Starting backend at http://127.0.0.1:8001
call :start_backend
if errorlevel 1 goto :start_failed

echo [3/5] Waiting for backend initialization...
call :wait_for_backend
if errorlevel 1 goto :backend_readiness_failed

echo [4/5] Starting frontend at http://127.0.0.1:5173
call :start_frontend
if errorlevel 1 goto :start_failed

echo [5/5] Waiting for frontend readiness...
call :wait_for_services
if errorlevel 1 goto :readiness_failed
set "RESTART_TOKEN=%RANDOM%%RANDOM%"
start "" "http://127.0.0.1:5173/?restart=%RESTART_TOKEN%"

echo.
echo [DONE] System restarted successfully.
echo        The browser should open at http://127.0.0.1:5173
echo        Backend and frontend service windows are minimized.
echo.
exit /b 0

:desktop_app
if exist "%ROOT%release\ZhiyuShield\ZhiyuShield.exe" (
    echo [INFO] Starting the packaged desktop application...
    start "" "%ROOT%release\ZhiyuShield\ZhiyuShield.exe"
    timeout /t 3 /nobreak >nul
    exit /b 0
)
echo [WARN] Packaged application was not found; using development mode.
set "MODE=dev"
goto :development_checks

:find_python
set "PYTHON_EXE="
if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE exit /b 1
"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0

:find_npm
set "NPM_EXE="
for /f "delims=" %%I in ('where npm.cmd 2^>nul') do if not defined NPM_EXE set "NPM_EXE=%%I"
if not defined NPM_EXE exit /b 1
exit /b 0

:start_backend
if not exist "%ROOT%logs" mkdir "%ROOT%logs" >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $args=@('-X','utf8','-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8001'); Start-Process -FilePath $env:PYTHON_EXE -ArgumentList $args -WorkingDirectory $env:ROOT -WindowStyle Hidden -RedirectStandardOutput ($env:ROOT+'logs\backend-console.log') -RedirectStandardError ($env:ROOT+'logs\backend-error.log') | Out-Null"
exit /b %errorlevel%

:start_frontend
if not exist "%ROOT%logs" mkdir "%ROOT%logs" >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $args=@('run','dev','--','--host','127.0.0.1'); Start-Process -FilePath $env:NPM_EXE -ArgumentList $args -WorkingDirectory ($env:ROOT+'frontend') -WindowStyle Hidden -RedirectStandardOutput ($env:ROOT+'logs\frontend-console.log') -RedirectStandardError ($env:ROOT+'logs\frontend-error.log') | Out-Null"
exit /b %errorlevel%

:stop_service
set "TARGET_PORT=%~1"
set "TARGET_KIND=%~2"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $port=[int]$env:TARGET_PORT; $kind=$env:TARGET_KIND; $ids=@(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | ForEach-Object {$_.OwningProcess} | Sort-Object -Unique); if(-not $ids){Write-Host ('  [OK] Port '+$port+' is already free.'); exit 0}; foreach($ownerPid in $ids){$proc=Get-CimInstance Win32_Process -Filter ('ProcessId='+$ownerPid); $cmd=[string]$proc.CommandLine; $owned=if($kind -eq 'backend'){($cmd -match 'uvicorn') -and ($cmd -match 'backend[.]app[.]main:app')}else{($cmd -match 'vite') -and ($cmd -match 'node_modules')}; if(-not $owned){Write-Host ('  [ERROR] Port '+$port+' is used by another program (PID '+$ownerPid+').'); exit 2}; Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue; Write-Host ('  [OK] Stopped '+$kind+' service on port '+$port+' (PID '+$ownerPid+').')}; for($i=0;$i -lt 40;$i++){if(-not (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)){exit 0}; Start-Sleep -Milliseconds 250}; Write-Host ('  [ERROR] Port '+$port+' did not become available.'); exit 3"
exit /b %errorlevel%

:wait_for_backend
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(90); do{try{$r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/api/health' -TimeoutSec 3; if($r.StatusCode -eq 200){Write-Host '  [OK] Backend is ready.'; exit 0}}catch{}; Start-Sleep -Seconds 1}while((Get-Date) -lt $deadline); Write-Host '  [ERROR] Backend did not become ready within 90 seconds.'; exit 1"
exit /b %errorlevel%

:wait_for_services
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(45); do{$backend=$false; $frontend=$false; try{$r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8001/api/health' -TimeoutSec 2; $backend=$r.StatusCode -eq 200}catch{}; try{$r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173/' -TimeoutSec 2; $frontend=$r.StatusCode -eq 200}catch{}; if($backend -and $frontend){Write-Host '  [OK] Backend and frontend are ready.'; exit 0}; Start-Sleep -Seconds 1}while((Get-Date) -lt $deadline); Write-Host '  [ERROR] Services did not become ready within 45 seconds.'; exit 1"
exit /b %errorlevel%

:bad_root
echo [ERROR] Cannot open the project directory.
goto :failed

:python_missing
echo [ERROR] Python was not found. Install Python 3.10 or 3.11 first.
goto :failed

:npm_missing
echo [ERROR] npm was not found. Install Node.js first.
goto :failed

:backend_missing
echo [ERROR] backend\app\main.py was not found under the project root.
goto :failed

:frontend_missing
echo [ERROR] frontend\package.json was not found under the project root.
goto :failed

:modules_missing
echo [ERROR] Frontend dependencies are missing.
echo         Run: cd /d "%ROOT%frontend" ^&^& npm ci
goto :failed

:python_packages_missing
echo [ERROR] Backend packages are missing.
echo         Run: "%PYTHON_EXE%" -m pip install -r requirements.txt
goto :failed

:npm_broken
echo [ERROR] npm is installed but could not run.
goto :failed

:preflight_failed
echo [ERROR] Startup preflight failed.
echo         Check the writable paths and port details shown above.
goto :failed

:restart_failed
echo [ERROR] Existing services could not be stopped safely.
echo         If a port belongs to another program, close it or change the configured ports.
goto :failed

:readiness_failed
echo [ERROR] The services were started but did not become ready in time.
echo         Review logs\backend-error.log and logs\frontend-error.log.
goto :failed

:backend_readiness_failed
echo [ERROR] The backend was started but did not become ready in time.
echo         Review logs\backend-error.log for details.
goto :failed

:start_failed
echo [ERROR] A service window could not be started.

:failed
echo.
pause
exit /b 1
