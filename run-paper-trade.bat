@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%.venv312\Scripts\python.exe"
set "CAPITAL=%~1"
set "HISTORY_DAYS=%~2"
set "FX_JPY_PER_USD=%~3"

if "%CAPITAL%"=="" set "CAPITAL=100000"
if "%HISTORY_DAYS%"=="" set "HISTORY_DAYS=2200"

if not exist "%PYTHON%" (
    echo Python executable not found: %PYTHON%
    exit /b 1
)

set "MOOMOO_BOT_EXECUTION_MODE=paper"

echo Starting PAPER one-shot trade via Moomoo OpenD demo account...

if "%FX_JPY_PER_USD%"=="" (
    "%PYTHON%" -m moomoo_bot.cli paper-trade --capital %CAPITAL% --history-days %HISTORY_DAYS%
) else (
    "%PYTHON%" -m moomoo_bot.cli paper-trade --capital %CAPITAL% --history-days %HISTORY_DAYS% --fx-jpy-per-usd %FX_JPY_PER_USD%
)
exit /b %errorlevel%
