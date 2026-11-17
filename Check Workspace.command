#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
bash "Start Dashboard.command" --preflight-only
PY=career-dashboard/backend/.venv/bin/python
"$PY" -m pip install -r career-dashboard/backend/requirements-dev.txt
(cd career-dashboard && "${PWD}/../$PY" -m pytest -q)
"$PY" scripts/check_profiles.py
"$PY" career-dashboard/backend/scripts/validate_workspace.py
(cd career-dashboard/frontend && npm test && npm run build)
"$PY" scripts/scan_release.py
"$PY" scripts/smoke_first_run.py
