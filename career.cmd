@echo off
rem One command for every AI app on Windows: career.cmd <command> ... (see AGENTS.md; "career.cmd help" lists them).
setlocal
set "APP=%~dp0career-dashboard"
set "CLI=%APP%\backend\scripts\career_cli.py"
if exist "%APP%\backend\.venv\Scripts\python.exe" (
  "%APP%\backend\.venv\Scripts\python.exe" "%CLI%" %*
  goto :done
)
rem Before Start Dashboard has run: Python 3.12, else any Python ("career doctor" names what to install).
py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 (
  python "%CLI%" %*
) else (
  py -3.12 "%CLI%" %*
)
:done
exit /b %ERRORLEVEL%
