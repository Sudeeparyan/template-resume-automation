#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 scripts/bootstrap.py "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/bootstrap.py "$@"
fi
echo "Install Python 3.12, then start again. See README.md." >&2
exit 1
