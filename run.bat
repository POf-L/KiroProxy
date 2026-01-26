@echo off
setlocal
chcp 65001 >nul
echo ========================================
echo   Starting KiroProxy Service...
echo ========================================

REM Default port is 6696 (override: set PORT env or run.bat 6696)
if not defined PORT set "PORT=6696"
if not "%~1"=="" set "PORT=%~1"

REM Set admin password (optional, leave empty for no login required)
REM If ADMIN_PASSWORD is already set in system environment, it won't be overwritten
if not defined ADMIN_PASSWORD set ADMIN_PASSWORD=

REM Find Python command (prefer python, fallback to py -3)
where python >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=py -3
    ) else (
        echo Python not found. Please install Python and add to PATH.
        goto end
    )
)

echo [1/3] Starting service... (PORT=%PORT%)
start "" /B %PYTHON% run.py %PORT%

echo [2/3] Waiting for service...
set /a count=0
set /a max_attempts=30

:check_service
set /a count+=1
echo Checking service status... (%count%/%max_attempts%)

REM Use curl to check if service is available (fallback to powershell)
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:%PORT% 2>nul | findstr "200" >nul
if not errorlevel 1 (
    echo [3/3] Service ready, opening browser...
    timeout /t 1 /nobreak >nul
    start http://127.0.0.1:%PORT%
    echo.
    echo ========================================
    echo   KiroProxy started successfully!
    echo   Web address: http://127.0.0.1:%PORT%
    echo ========================================
    goto end
)

REM If curl is not available, use PowerShell
powershell -Command "try { $response = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%' -TimeoutSec 2 -UseBasicParsing; if ($response.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo [3/3] Service ready, opening browser...
    timeout /t 1 /nobreak >nul
    start http://127.0.0.1:%PORT%
    echo.
    echo ========================================
    echo   KiroProxy started successfully!
    echo   Web address: http://127.0.0.1:%PORT%
    echo ========================================
    goto end
)

if %count% lss %max_attempts% (
    timeout /t 2 /nobreak >nul
    goto check_service
)

echo.
echo ========================================
echo   Warning: Service startup timeout
echo   Please visit: http://127.0.0.1:%PORT%
echo ========================================

:end
if "%KIROPROXY_NO_PAUSE%"=="1" exit /b 0
pause
endlocal
