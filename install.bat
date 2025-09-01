@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo === gpt5-vs-x-spam installer ===

REM Resolve repo root and key paths
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "VENV_DIR=%BACKEND%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

echo [1/6] Checking Python...
set "PYTHON_CMD=python"
where %PYTHON_CMD% >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python not found in PATH. Install Python 3.11+ and rerun.
    exit /b 1
  ) else (
    set "PYTHON_CMD=py -3"
  )
)

echo [2/6] Creating virtual environment at backend\.venv ...
if not exist "%VENV_DIR%" (
  %PYTHON_CMD% -m venv "%VENV_DIR%"
  if errorlevel 1 goto :err
) else (
  echo Virtual environment already exists.
)

echo [3/6] Upgrading pip, setuptools, wheel...
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :err

echo [4/6] Installing backend requirements...
"%VENV_PIP%" install -r "%BACKEND%\requirements.txt"
if errorlevel 1 goto :err

echo [5/6] Installing Playwright browsers (Chromium)...
"%VENV_PY%" -m playwright install
if errorlevel 1 goto :err

echo [6/6] Preparing .env and data directories...
if not exist "%BACKEND%\data" mkdir "%BACKEND%\data" >nul 2>&1
if not exist "%BACKEND%\data\cache" mkdir "%BACKEND%\data\cache" >nul 2>&1

if not exist "%BACKEND%\.env" (
  if exist "%ROOT%.env" (
    copy "%ROOT%.env" "%BACKEND%\.env" >nul
    echo Copied existing .env to backend\.env
  ) else (
    if exist "%ROOT%.env.example" (
      copy "%ROOT%.env.example" "%BACKEND%\.env" >nul
      echo Created backend\.env from .env.example
    ) else (
      echo No .env or .env.example found. Skipping .env setup.
    )
  )
) else (
  echo backend\.env already exists. Skipping.
)

echo.
echo Install complete.
echo - Activate venv:  backend\^.venv\Scripts\activate
echo - Run API:       backend\run_api.bat
echo - Frontend:      cd frontend ^& python -m http.server 5500
echo.
exit /b 0

:err
echo.
echo Install failed with error %errorlevel%.
exit /b %errorlevel%

