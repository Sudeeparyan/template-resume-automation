#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# No Python 3.12: offer Homebrew's when someone is at the window (never during --preflight-only checks).
if ! command -v python3.12 >/dev/null 2>&1 && command -v brew >/dev/null 2>&1 \
   && [ -t 0 ] && [ "${1:-}" != "--preflight-only" ] && [ -z "${CAREER_NO_INSTALL:-}" ]; then
  answer=n
  read -r -p "The app needs Python 3.12. Install it now with Homebrew (brew install python@3.12)? [Y/n] " answer || answer=n
  case "${answer:-y}" in
    [Yy]*) brew install python@3.12 || echo "Homebrew could not install Python 3.12; see README.md." >&2 ;;
  esac
fi
if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 scripts/bootstrap.py "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/bootstrap.py "$@"
fi
echo "Install Python 3.12 from https://www.python.org/downloads/ (or Homebrew: brew install python@3.12), then start again. See README.md." >&2
exit 1
