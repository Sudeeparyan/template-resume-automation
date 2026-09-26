@echo off
rem Morning jobs: search, check and prepare jobs, then write MORNING-JOBS.md. See AUTOPILOT.md.
rem   morning-jobs.cmd               every profile with Morning jobs on (else the last opened one)
rem   morning-jobs.cmd --list-only   only rewrite the list (under a minute)
rem   morning-jobs.cmd --background  start it and return at once (for AI app schedulers)
rem   morning-jobs.cmd --check       health check only
rem   morning-jobs.cmd --jobs 5      find 5 new jobs now, each with a tailored resume
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "PY=%~dp0..\career-dashboard\backend\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo The app is not set up on this PC yet; setting it up first. This takes a few minutes once.
  call "%~dp0..\Start Dashboard.cmd" --preflight-only >> "%~dp0logs\setup.log" 2>&1
)
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0autopilot.py" %*
exit /b %errorlevel%
