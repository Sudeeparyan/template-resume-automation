@echo off
rem One command for every AI app on Windows: career.cmd <command> ... (see AGENTS.md; "career.cmd help" lists them).
setlocal
set "APP=%~dp0career-dashboard"
set "CLI=%APP%\backend\scripts\career_cli.py"
if exist "%APP%\backend\.venv\Scripts\python.exe" (
  "%APP%\backend\.venv\Scripts\python.exe" "%CLI%" %*
) else (
  py -3.12 "%CLI%" %*
)
exit /b %ERRORLEVEL%
