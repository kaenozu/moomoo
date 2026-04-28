@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%.venv312\Scripts\python.exe"
set "REQUESTED_PORT=%~1"
set "PORT=%REQUESTED_PORT%"

if not exist "%PYTHON%" (
    echo Python executable not found: %PYTHON%
    exit /b 1
)

set "MOOMOO_BOT_EXECUTION_MODE=paper"

if "%PORT%"=="" (
    for %%P in (8501 8502 8600 8601 8602) do (
        netstat -ano -p tcp | findstr /r /c:":%%P .*LISTENING" >nul
        if errorlevel 1 (
            set "PORT=%%P"
            goto :port_found
        )
    )
)

:port_found
if "%PORT%"=="" (
    echo No available Streamlit port found.
    exit /b 1
)

echo Starting PAPER studio on port %PORT%...
"%PYTHON%" -m streamlit run "%ROOT%src\moomoo_bot\ui\paper_studio.py" --server.port %PORT%
exit /b %errorlevel%
