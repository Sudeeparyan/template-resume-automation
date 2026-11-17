@echo off
setlocal
title Career Workspace Practice Demo
cd /d "%~dp0"

set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>&1 && set "PY=python")
if not defined PY (
  echo Python 3.12 was not found. Install Python 3.12 from python.org, then start again.
  goto :error
)
%PY% "%~dp0scripts\bootstrap.py" --demo %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Practice setup stopped. Read the message above and README.md.
if "%~1"=="--preflight-only" exit /b 1
pause
exit /b 1
