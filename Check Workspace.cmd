@echo off
setlocal
cd /d "%~dp0"
call "%~dp0Start Dashboard.cmd" --preflight-only || goto :error
set "PY=career-dashboard\backend\.venv\Scripts\python.exe"
"%PY%" -m pip install -r career-dashboard\backend\requirements-dev.txt || goto :error
pushd career-dashboard
"%~dp0%PY%" -m pytest -q || (popd & goto :error)
popd
"%PY%" scripts\check_profiles.py || goto :error
"%PY%" career-dashboard\backend\scripts\validate_workspace.py || goto :error
pushd career-dashboard\frontend
call npm.cmd test || (popd & goto :error)
call npm.cmd run build || (popd & goto :error)
popd
"%PY%" scripts\scan_release.py || goto :error
"%PY%" scripts\smoke_first_run.py || goto :error
echo All checks passed.
exit /b 0

:error
echo Checks failed. See the message above.
exit /b 1
