@echo off
setlocal
chcp 65001 >nul 2>&1

set "ROOT=%~dp0.."
set "PY=%ROOT%\backend\.venv\Scripts\python.exe"

if not exist "%PY%" (
  set "PY=python"
)

"%PY%" "%ROOT%\scripts\verify_all.py" --python "%PY%" %*
exit /b %ERRORLEVEL%
