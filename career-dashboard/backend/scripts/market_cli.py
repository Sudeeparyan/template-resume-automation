#!/usr/bin/env python3
"""The shared market store the Tracker reads: `career market status|refresh [--force]`.

  career market status            postings by state, the last refresh and whether one is due
  career market refresh [--force] read the public sources for the roles of the ready profiles on this
                                  computer and record what they list (no AI; graphs/tracker_refresh.py)

Public data only (data/market/market.db); no profile's jobs change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(APP), str(APP / "backend/scripts")]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["status", "refresh"])
    parser.add_argument("--force", action="store_true", help="refresh even when the last one is recent")
    args = parser.parse_args(argv)
    from backend import telemetry
    from backend.graphs import tracker_refresh
    from backend.market.store import MarketStore

    telemetry.setup_tracing()
    if args.command == "status":
        store = MarketStore()
        result = {"counts": store.counts(), "last_refresh": store.last_run(tracker_refresh.NAME),
                  "refresh_due": tracker_refresh.due(), "every_hours": tracker_refresh.REFRESH_HOURS}
    else:
        print("Reading the public sources (no AI). This can take several minutes...", file=sys.stderr, flush=True)
        result = tracker_refresh.run(force=args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
