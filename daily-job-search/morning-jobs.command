#!/bin/bash
# Morning jobs: search, check and prepare jobs, then write MORNING-JOBS.md. See AUTOPILOT.md.
#   bash morning-jobs.command               every profile with Morning jobs on (else the last opened one)
#   bash morning-jobs.command --list-only   only rewrite the list (under a minute)
#   bash morning-jobs.command --background  start it and return at once (for AI app schedulers)
#   bash morning-jobs.command --check       health check only
#   bash morning-jobs.command --jobs 5      find 5 new jobs now, each with a tailored resume
set -uo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p logs
PY="../career-dashboard/backend/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "The app is not set up on this computer yet; setting it up first. This takes a few minutes once."
  bash "../Start Dashboard.command" --preflight-only >> logs/setup.log 2>&1
fi
[ -x "$PY" ] || PY="python3"
exec "$PY" autopilot.py "$@"
