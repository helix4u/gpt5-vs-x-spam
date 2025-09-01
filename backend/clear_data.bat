@echo off
setlocal EnableExtensions
pushd "%~dp0"
set "DATA=data"

if exist "%DATA%\dataset.jsonl" del /f /q "%DATA%\dataset.jsonl"
if exist "%DATA%\results.jsonl" del /f /q "%DATA%\results.jsonl"
if exist "%DATA%\cache" del /f /q "%DATA%\cache\*" >nul 2>&1

echo Cleared dataset, results, and cache in %CD%\%DATA%.
popd
exit /b 0

