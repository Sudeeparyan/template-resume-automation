@echo off
rem The overnight hunt for the selected or explicitly named profile.
rem Example: night-hunt.cmd --target 10 --hours 8 --min-fit 75
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "PY=%~dp0..\career-dashboard\backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0night_hunt.py" %* >> "%~dp0logs\night-hunt.log" 2>&1
exit /b %errorlevel%
