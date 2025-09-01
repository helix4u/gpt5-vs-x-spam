@echo off
setlocal EnableExtensions
REM change to this script's directory (backend) so Python can import app.*
pushd "%~dp0"

python run_api.py

popd
