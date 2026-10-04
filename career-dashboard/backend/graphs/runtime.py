"""What every graph node gets besides its state.

LangGraph hands this to each node as ``runtime.context`` (``StateGraph(..., context_schema=
GraphContext)``). It is never checkpointed: a run resumed after a restart gets fresh services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphContext:
    services: Any                 # the profile's CareerServices
    runner: Any                   # the AgentRunner: AI calls (cache, gateway, metering) and the trace
    run_id: str = ""              # the agent run this graph executes, for its trace lines
    market: Any = None            # backend.market.store.MarketStore (shared public data)
    fetcher: Any = None           # backend.services.job_sources.Fetcher for verifying cited pages
    url_check: Any = None         # url -> bool: a public web address (salary_research.public_url)
    flags: dict = field(default_factory=dict)
    fresh: bool = False           # a rerun: ask the AI again and research again, never reuse saved answers

    def market_store(self):
        if self.market is None:
            from backend.market.store import MarketStore

            self.market = MarketStore()
        return self.market

    def page_fetcher(self):
        """A polite fetcher that refuses redirects to private network addresses."""
        if self.fetcher is None:
            from urllib.request import build_opener

            from backend.services.job_sources import Fetcher
            from backend.services.salary_research import PublicRedirects

            self.fetcher = Fetcher(opener=build_opener(PublicRedirects()).open)
        return self.fetcher

    def is_public(self, url: str) -> bool:
        if self.url_check is None:
            from backend.services.salary_research import public_url

            self.url_check = public_url
        return bool(self.url_check(url))
