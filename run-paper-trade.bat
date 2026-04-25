@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%.venv312\Scripts\python.exe"
set "HOST=%MOOMOO_BOT_OPEND_HOST%"
set "PORT=%MOOMOO_BOT_OPEND_PORT%"
set "CAPITAL=%~1"
set "HISTORY_DAYS=%~2"
set "FX_JPY_PER_USD=%~3"

if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=11111"
if "%CAPITAL%"=="" set "CAPITAL=280000"
if "%HISTORY_DAYS%"=="" set "HISTORY_DAYS=2200"
if "%CAPITAL_CURRENCY%"=="" set "CAPITAL_CURRENCY=JPY"

echo WARNING: This will run paper trading with %CAPITAL% %CAPITAL_CURRENCY% input capital.
echo If this is incorrect, press Ctrl+C now to cancel.
timeout /t 3 /nobreak >nul 2>&1

if not exist "%PYTHON%" (
    echo Python executable not found: %PYTHON%
    exit /b 1
)

set "MOOMOO_BOT_OPEND_HOST=%HOST%"
set "MOOMOO_BOT_OPEND_PORT=%PORT%"
set "MOOMOO_BOT_CAPITAL_CURRENCY=%CAPITAL_CURRENCY%"

echo Starting paper-trade monitor against OpenD at %MOOMOO_BOT_OPEND_HOST%:%MOOMOO_BOT_OPEND_PORT% with %CAPITAL% %CAPITAL_CURRENCY% input...

if "%FX_JPY_PER_USD%"=="" (
    "%PYTHON%" -m moomoo_bot.cli auto-run --capital %CAPITAL% --history-days %HISTORY_DAYS%
) else (
    "%PYTHON%" -m moomoo_bot.cli auto-run --capital %CAPITAL% --history-days %HISTORY_DAYS% --fx-jpy-per-usd %FX_JPY_PER_USD%
)
exit /b %errorlevel%
