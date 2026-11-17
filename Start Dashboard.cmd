@echo off
setlocal
title Career Workspace
cd /d "%~dp0"

rem Python 3.12 from the py launcher, else a python on PATH that is 3.12 (python.org offers newer ones first).
call :find_python
rem Missing: offer to install it (never during Check Workspace's --preflight-only run or with CAREER_NO_INSTALL set).
if not defined PY if not "%~1"=="--preflight-only" if not defined CAREER_NO_INSTALL call :install_python
if not defined PY (
  echo Python 3.12 was not found. Install Python 3.12 from https://www.python.org/downloads/ then start again.
  echo Newer versions such as 3.13 or 3.14 cannot run the app's pinned OCR packages yet.
  goto :error
)
%PY% "%~dp0scripts\bootstrap.py" %*
if errorlevel 1 goto :error
exit /b 0

:find_python
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>&1 && set "PY=python")
rem An install made after this window opened is not on its PATH yet, so look in python.org's own folder.
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY="%ProgramFiles%\Python312\python.exe""
exit /b 0

:install_python
where winget >nul 2>&1 || exit /b 0
echo.
echo The app needs Python 3.12, and it is not installed on this computer.
rem No answer within two minutes (nobody at the window) counts as No: nothing is installed unasked.
choice /c YN /t 120 /d N /m "Install Python 3.12 now (from python.org, through winget)"
if errorlevel 2 exit /b 0
winget install --id Python.Python.3.12 -e --source winget --scope user --accept-package-agreements --accept-source-agreements
call :find_python
if defined PY exit /b 0
rem Some computers only offer the all-users installer, which asks for permission first.
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
call :find_python
exit /b 0

:error
echo.
echo Setup stopped. Read the message above and the setup guide in README.md.
if "%~1"=="--preflight-only" exit /b 1
pause
exit /b 1
