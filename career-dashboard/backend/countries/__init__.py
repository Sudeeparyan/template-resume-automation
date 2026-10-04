"""Country packs: everything that changes when a candidate searches in another country.

A pack is a folder under backend/countries/<code>/ holding `pack.yml` (names, time
zone, paper, spelling, location matching, wording) and the templates a new profile
starts from (`sponsorship.yml`, `regions.yml`, `portals.yml`, `guides/*.md`). The
intake copies those templates into the profile's own data/config/, so each profile
owns and can edit its rules; the pack itself is never written.

A workspace's pack comes from `country_pack` in its profile.yml, else from
`location_preferences.country`. Each profile selects its own pack independently.

Which packs this copy offers is one switch, `countries/markets.yml` (`enabled: [ie]`);
the environment variable CAREER_MARKETS (for example "ie,us") overrides it for
development and tests. A pack that is switched off stays in the code: its profiles keep
their own rules, but searching is refused until the market is enabled again.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.paths import COUNTRIES

DEFAULT_CODE = "ie"
MARKETS_FILE = COUNTRIES / "markets.yml"


@dataclass(frozen=True)
class Pack:
    code: str
    name: str
    data: dict = field(repr=False)

    # --- identity --------------------------------------------------------
    @property
    def folder(self) -> Path:
        return COUNTRIES / self.code

    @property
    def adjective(self) -> str:
        """'US' / 'Irish': as in 'US postings'."""
        return str(self.data.get("adjective") or self.name)

    @property
    def names(self) -> list[str]:
        return [self.name, *[str(n) for n in self.data.get("names") or []]]

    @property
    def timezone(self) -> str:
        return str(self.data.get("timezone") or "UTC")

    @property
    def paper(self) -> str:
        return str(self.data.get("paper") or "letter")

    @property
    def paper_label(self) -> str:
        return str(self.data.get("paper_label") or ("US Letter" if self.paper == "letter" else "A4"))

    @property
    def spelling(self) -> str:
        return str(self.data.get("spelling") or "")

    @property
    def sponsor_index(self) -> str | None:
        """The public employer history this country uses ('uscis', 'dete'); None without one."""
        return self.data.get("sponsor_index") or None

    @property
    def sponsor_history_required(self) -> bool:
        """True where mid-size and large employers need a public sponsorship record to be listed
        (the US H-1B history). Elsewhere history only ranks: a silent posting is never dropped
        for an employer's missing record (Ireland's DETE permits)."""
        return self.data.get("sponsor_history") == "required"

    @property
    def tier_labels(self) -> dict:
        """The tier wording of this country's gate (sponsorship.yml `tier_labels`); empty
        where the page's own wording applies (the US pack)."""
        try:
            rules = yaml.safe_load(self.template("sponsorship.yml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return {str(k): str(v) for k, v in (rules.get("tier_labels") or {}).items()}

    def text(self, key: str, **values: Any) -> str:
        """A wording from the pack's `messages`, with {placeholders} filled."""
        template = str((self.data.get("messages") or {}).get(key, ""))
        return template.format(**values) if values else template

    # --- location --------------------------------------------------------
    def location_ok(self, location: str) -> bool:
        """True when the posting's location is in this country (or remote within it)."""
        if self.code == "us":
            from backend.job_quality import is_us_location

            return is_us_location(location)
        return _matcher(self.code).matches(location or "")

    def open_remote(self, location: str) -> bool:
        """A bare or region-wide 'Remote': not a refusal, checked at research time."""
        patterns = (self.data.get("location") or {}).get("open_remote") or [r"\s*remote\s*"]
        return any(re.fullmatch("(?i)" + p, location or "") for p in patterns)

    # --- templates a new profile starts from ----------------------------
    def template(self, relative: str) -> Path:
        return self.folder / relative


class _Matcher:
    """Location matching from a pack's `location` block (all regex fragments)."""

    def __init__(self, spec: dict):
        def alternation(key):
            items = [str(i) for i in spec.get(key) or []]
            return re.compile(r"(?i)\b(?:" + "|".join(items) + r")\b") if items else None

        self.strong = alternation("strong")          # the country's own name(s)
        self.places = alternation("places")          # cities, counties, regions
        self.excluded_places = alternation("excluded_places")  # named regions outside this jurisdiction
        self.remote = alternation("remote")          # "Remote (Ireland)" and the like
        self.strip = alternation("strip_before_match")  # "Northern Ireland" is not Ireland
        self.foreign = alternation("foreign")        # other countries that outweigh a bare city
        self.twin_markers = [re.compile(p) for p in spec.get("twin_markers") or []]

    def matches(self, location: str) -> bool:
        text = self.strip.sub(" ", location) if self.strip else location
        if (self.excluded_places and self.excluded_places.search(text)
                and not (self.places and self.places.search(text))):
            # A broad "Ireland" label cannot turn Belfast into a Republic role.
            # A posting naming Dublin or Cork as another allowed place still counts.
            return False
        if self.strong and self.strong.search(text):
            return True
        if self.remote and self.remote.search(text):
            return True
        if not (self.places and self.places.search(text)):
            return False
        # A city that also exists elsewhere (Dublin, OH; Waterford, MI) needs no foreign marker.
        if any(marker.search(location) for marker in self.twin_markers):
            return False
        return not (self.foreign and self.foreign.search(text))


@lru_cache(maxsize=None)
def _matcher(code: str) -> _Matcher:
    return _Matcher(load_pack(code).data.get("location") or {})


@lru_cache(maxsize=None)
def load_pack(code: str) -> Pack:
    code = (code or DEFAULT_CODE).lower()
    path = COUNTRIES / code / "pack.yml"
    if not path.is_file():
        raise ValueError(f"No country pack '{code}'")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Pack(code=code, name=str(data.get("name") or code.upper()), data=data)


def known_markets() -> tuple[str, ...]:
    """Every country pack in this copy, switched on or not."""
    return tuple(sorted(p.name for p in COUNTRIES.iterdir() if (p / "pack.yml").is_file()))


def enabled_markets() -> list[str]:
    """The markets this copy offers, in preference order (never empty)."""
    try:
        stamp = MARKETS_FILE.stat().st_mtime_ns
    except OSError:
        stamp = 0
    return list(_enabled(os.environ.get("CAREER_MARKETS", ""), stamp))


@lru_cache(maxsize=8)
def _enabled(override: str, stamp: int) -> tuple[str, ...]:
    if override.strip():
        wanted = [code.strip().lower() for code in override.split(",")]
    else:
        try:
            data = yaml.safe_load(MARKETS_FILE.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        wanted = [str(code).strip().lower() for code in (data.get("enabled") or [])] if isinstance(data, dict) else []
    known = known_markets()
    return tuple(dict.fromkeys(code for code in wanted if code in known)) or (DEFAULT_CODE,)


def is_enabled(code: str | None) -> bool:
    return str(code or "").strip().lower() in enabled_markets()


def available(include_disabled: bool = False) -> list[Pack]:
    """The packs a person can choose from (all packs in the code with include_disabled)."""
    return [load_pack(code) for code in (known_markets() if include_disabled else enabled_markets())]


def code_for(profile: dict | None) -> str:
    """The pack a profile uses: `country_pack`, else its target country's name.

    Switched-off packs still resolve, so an older profile keeps its own rules instead of
    silently becoming another country's profile.
    """
    profile = profile if isinstance(profile, dict) else {}
    code = str(profile.get("country_pack") or "").strip().lower()
    if code and (COUNTRIES / code / "pack.yml").is_file():
        return code
    country = str((profile.get("location_preferences") or {}).get("country") or "").strip().casefold()
    if country:
        for pack in available(include_disabled=True):
            if country in {n.casefold() for n in pack.names} or country == pack.code:
                return pack.code
    return DEFAULT_CODE


def target_markets_for(root) -> list[str]:
    """The markets this person selected, in preference order."""
    path = Path(root) / "data/config/profile.yml"
    try:
        profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        profile = {}
    profile = profile if isinstance(profile, dict) else {}
    configured = profile.get("target_markets")
    if not isinstance(configured, list) or not configured:
        configured = [code_for(profile)]
    known = known_markets()
    markets = list(dict.fromkeys(str(code).lower() for code in configured if str(code).lower() in known))
    # Switched-off markets drop out; a profile that only has switched-off markets keeps
    # them, so require_enabled_markets can say so instead of searching another country.
    return [code for code in markets if is_enabled(code)] or markets or [DEFAULT_CODE]


def require_enabled_markets(root, market: str | None = None) -> None:
    """Searching and preparing need at least one market this copy offers."""
    markets = [market] if market else target_markets_for(root)
    if not any(is_enabled(code) for code in markets):
        names = ", ".join(load_pack(code).name for code in markets)
        offered = ", ".join(load_pack(code).name for code in enabled_markets())
        raise ValueError(f"This profile searches {names}, which this copy no longer offers (it searches {offered}). "
                         "Create a separate profile for an offered market, or see docs/DEVELOPERS.md, \"Re-enabling a market\".")


def require_known_authorization(root, market: str | None = None) -> None:
    """New profiles must supply eligibility facts before screening or applications.

    A market this copy no longer offers is refused first: its searches stay off.
    """
    if market and not is_enabled(market):
        raise ValueError(f"{load_pack(market).name} is switched off in this copy, so its jobs are not searched or "
                         "prepared. See docs/DEVELOPERS.md, \"Re-enabling a market\".")
    if not market:
        require_enabled_markets(root)
    path = Path(root) / "data/config/profile.yml"
    try:
        profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        profile = {}
    profile = profile if isinstance(profile, dict) else {}
    by_market = profile.get("work_authorization_by_market")
    by_market = by_market if isinstance(by_market, dict) else {}
    selected = [market] if market else target_markets_for(root)
    for code in selected:
        facts = by_market.get(code) or {}
        facts = facts if isinstance(facts, dict) else {}
        if facts.get("status") not in {"authorized", "needs_sponsorship"} or facts.get("citizenship") not in {"citizen", "noncitizen"}:
            raise ValueError(f"Confirm work authorization and citizenship for {load_pack(code).name} in Assistant → Profile sources → Build settings, then rebuild before eligibility-dependent actions.")
        if (facts["status"] == "authorized" and facts["citizenship"] != "citizen"
                and facts.get("needs_sponsorship_later") not in {"yes", "no"}):
            raise ValueError(f"Confirm whether employer sponsorship will be needed later for {load_pack(code).name} in Assistant → Profile sources → Build settings, then rebuild before eligibility-dependent actions.")
        if code == "ie" and facts.get("permission_type") == "stamp_1g" and (
                not facts.get("valid_until") or facts.get("valid_until_confirmed") is not True):
            raise ValueError("Confirm the exact Stamp 1G expiry date in Build settings before searching or preparing jobs.")
        if facts["status"] == "authorized" and facts.get("valid_until"):
            from datetime import date, datetime
            from zoneinfo import ZoneInfo

            try:
                expiry = date.fromisoformat(facts["valid_until"])
            except (TypeError, ValueError):
                raise ValueError(f"Confirm the permission validity date for {load_pack(code).name} before eligibility-dependent actions.") from None
            if expiry < datetime.now(ZoneInfo(load_pack(code).timezone)).date():
                raise ValueError(f"The recorded permission for {load_pack(code).name} expired on {expiry.isoformat()}. Confirm current authorization before eligibility-dependent actions.")


def market_for_location(root, location: str, requested: str | None = None, *, require_match: bool = False) -> str:
    """Choose a selected market; require a location match for newly saved postings.

    Older records can lack a useful location, so callers migrating or reading them
    retain the primary-market fallback. New postings must establish their market
    from the posting itself instead of silently inheriting that fallback.
    """
    markets = target_markets_for(root)
    if requested:
        if requested not in markets:
            raise ValueError(f"Market '{requested}' is not selected for this profile")
        if require_match and not load_pack(requested).location_ok(location):
            raise ValueError(f"Posting location does not establish the selected {load_pack(requested).name} market.")
        return requested
    matches = [code for code in markets if load_pack(code).location_ok(location)]
    if require_match and len(matches) != 1:
        raise ValueError("Posting location does not establish one selected job market. Confirm its location before saving.")
    return matches[0] if matches else markets[0]


_PROFILE_CACHE: dict[str, tuple[int, str]] = {}


def pack_for(root, market: str | None = None) -> Pack:
    """The selected pack, or the primary pack of a workspace."""
    if market:
        if market not in target_markets_for(root):
            raise ValueError(f"Market '{market}' is not selected for this profile")
        return load_pack(market)
    path = Path(root) / "data/config/profile.yml"
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return load_pack(DEFAULT_CODE)
    cached = _PROFILE_CACHE.get(str(path))
    if not cached or cached[0] != stamp:
        try:
            profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            profile = {}
        cached = (stamp, code_for(profile))
        _PROFILE_CACHE[str(path)] = cached
    return load_pack(cached[1])
