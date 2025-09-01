@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo === gpt5-vs-x-spam dev start ===
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "VENV_PY=%BACKEND%\.venv\Scripts\python.exe"

REM 1) Ensure venv + deps are installed
if not exist "%VENV_PY%" (
  echo No venv found. Running installer...
  call "%ROOT%install.bat" || goto :err
)

REM 2) Ensure Playwright browsers are installed
call "%BACKEND%\install_playwright.bat" || goto :err

REM 3) Start API (no reload) with Windows selector loop policy
set "WIN_LOOP_POLICY=selector"
start "gpt5-api" cmd /c "cd /d "%BACKEND%" && "%VENV_PY%" run_api.py"

REM 4) Start frontend static server on port 5500
start "gpt5-ui" cmd /c "cd /d "%FRONTEND%" && "%VENV_PY%" -m http.server 5500 --bind 0.0.0.0"

REM 5) Open the UI
start "" http://127.0.0.1:5500/

echo.
echo Servers starting... API at http://127.0.0.1:8000  UI at http://127.0.0.1:5500/
echo Close windows to stop them.
exit /b 0

:err
echo.
echo Start failed with error %errorlevel%.
exit /b %errorlevel%
