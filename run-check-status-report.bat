@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%.venv312\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Python executable not found: %PYTHON%
    exit /b 1
)

set "MODE=%~1"
set "STATE_DB=%~2"
set "FILLS_LIMIT=%~3"
set "ORDERS_LIMIT=%~4"
set "REALIZATIONS_LIMIT=%~5"

if "%MODE%"=="" set "MODE=paper"
if "%FILLS_LIMIT%"=="" set "FILLS_LIMIT=10"
if "%ORDERS_LIMIT%"=="" set "ORDERS_LIMIT=10"
if "%REALIZATIONS_LIMIT%"=="" set "REALIZATIONS_LIMIT=10"

if /I not "%MODE%"=="paper" if /I not "%MODE%"=="live" (
    echo Invalid mode: %MODE%
    echo Usage: run-check-status-report.bat [paper^|live] [state_db_path] [fills_limit] [orders_limit] [realizations_limit]
    exit /b 1
)

set "MOOMOO_BOT_EXECUTION_MODE=%MODE%"
if not "%STATE_DB%"=="" set "MOOMOO_BOT_STATE_DB_PATH=%STATE_DB%"

echo ==================================================
echo Moomoo Bot One-Touch Check
echo Mode: %MOOMOO_BOT_EXECUTION_MODE%
if not "%STATE_DB%"=="" (
    echo State DB override: %MOOMOO_BOT_STATE_DB_PATH%
) else (
    echo State DB override: ^(none^)
)
echo ==================================================
echo.

echo [1/2] status
"%PYTHON%" -m moomoo_bot status
if errorlevel 1 exit /b %errorlevel%

echo.
echo [2/2] execution-report
"%PYTHON%" -m moomoo_bot execution-report --fills-limit %FILLS_LIMIT% --orders-limit %ORDERS_LIMIT% --realizations-limit %REALIZATIONS_LIMIT%
exit /b %errorlevel%
