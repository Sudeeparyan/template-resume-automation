@echo off
setlocal
title Career Workspace
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
  py -3.12 "%~dp0scripts\bootstrap.py" %*
  if errorlevel 1 goto :error
  exit /b 0
)
where python >nul 2>&1
if errorlevel 1 (
  echo Python not found. Install Python 3.12 from python.org, then start again.
  goto :error
)
python "%~dp0scripts\bootstrap.py" %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Setup stopped. Read the message above and the setup guide in README.md.
if "%~1"=="--preflight-only" exit /b 1
pause
exit /b 1
