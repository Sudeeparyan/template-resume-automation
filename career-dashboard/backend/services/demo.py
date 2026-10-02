"""Demo mode: a per-profile switch for POC demonstrations.

When on (Settings → Demo mode), the evidence gates (profile reconciliation, pending
Assurance decisions) and the discovery rejection gates (market match, work-permit refusal,
relevance, employer legitimacy, the fit bar) become advisory: postings are kept with a
"demo:" note instead of being dropped, and the ready-to-submit check can never return
"blocked". The never-re-apply rules, duplicate suppression and the known-authorization
requirement still apply, and the tailor still writes only evidenced wording. Off by
default, so existing profiles and tests are unchanged.
"""

from __future__ import annotations


def demo_mode(services) -> bool:
    """True when this profile has demo mode on (the ``demo_mode`` preference)."""
    return str(services.pref("demo_mode", "0")).lower() in ("1", "true", "yes", "on")
