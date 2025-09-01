@echo off
setlocal EnableExtensions
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo venv missing. run install.bat or create backend\.venv first.
  exit /b 1
)
".venv\Scripts\python.exe" -m playwright install
popd
echo Playwright installation finished.
