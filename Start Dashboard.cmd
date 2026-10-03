@echo off
setlocal
title Career Workspace
cd /d "%~dp0"

rem Python 3.12 from the py launcher, else a python on PATH that is 3.12 (python.org offers newer ones first).
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>&1 && set "PY=python")
if not defined PY (
  echo Python 3.12 was not found. Install Python 3.12 from python.org, then start again.
  echo Newer versions such as 3.13 or 3.14 cannot run the app's pinned OCR packages yet.
  goto :error
)
%PY% "%~dp0scripts\bootstrap.py" %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Setup stopped. Read the message above and the setup guide in README.md.
if "%~1"=="--preflight-only" exit /b 1
pause
exit /b 1
