"""Deterministic posting freshness, hard relevance gates, legitimacy and batch selection.

The fit score itself comes from the job's verified requirement matrix (services/fit.py)."""

from __future__ import annotations

import hashlib
import json
import re
import socket
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ATS_DOMAINS = {
    "ashbyhq.com",
    "bamboohr.com",
    "greenhouse.io",
    "jobs.lever.co",
    "myworkdayjobs.com",
    "recruitee.com",
    "smartrecruiters.com",
    "teamtailor.com",
    "workable.com",
    # More applicant-tracking systems that host employers' own postings (Rezoomo is Irish).
    "rezoomo.com",
    "personio.de",
    "personio.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "successfactors.eu",
    "jobvite.com",
    "breezy.hr",
    "pinpointhq.com",
    "oraclecloud.com",
}
# Irish job boards that publish the hiring employer with each posting (schema.org
# hiringOrganization). A posting read there still needs its employer's legal presence.
EMPLOYER_BOARDS = {"gradireland.com", "jobs.ie", "irishjobs.ie", "publicjobs.ie", "jobsireland.ie"}
# US states, abbreviations, big hubs and "remote (US)". Anything else is not pursued.
_STATES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|"
    "indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|"
    "montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|"
    "oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
    "west virginia|wisconsin|wyoming|district of columbia"
)
_ABBR = "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
_HUBS = r"new york city|nyc|san francisco|bay area|silicon valley|seattle|austin|boston|chicago|raleigh|durham|minneapolis|los angeles|san diego|san jose|denver|atlanta|dallas|houston|phoenix|portland|pittsburgh|philadelphia|washington,? d\.?c\.?"
# How a feed may end a location after the state: "Alameda, CA — on-site", "Austin, TX (Hybrid)". Only
# work-arrangement words, so a trailing country ("Munich, DE, Germany") still decides for itself.
_ARRANGEMENT = (r"(?:\s*[-–—|/;:(,]?\s*(?:on[- ]?site|in[- ]office|in[- ]person|hybrid|remote|"
                r"full[- ]time|part[- ]time)\s*\)?)*")
US_LOCATION = re.compile(
    r"(?i)\b(?:united states|u\.?s\.?a?\.?|usa|us[- ]remote|remote[- ,(]*(?:us|usa|united states)|" + _STATES + "|" + _HUBS + r")\b"
    r"|,\s*(?:" + _ABBR + r")\b(?:\s+\d{5})?\s*(?:,\s*(?:usa?|united states))?" + _ARRANGEMENT + r"\s*$"
    r"|\b(?:" + _ABBR + r")\s+\d{5}\b"
)
# Countries, regions and cities with no US namesake: only a strong US marker outweighs them.
_FOREIGN_STRONG = re.compile(
    r"(?i)\b(?:ireland|united kingdom|uk|canada|toronto|india|bangalore|bengaluru|hyderabad|chennai|pune|germany|france|"
    r"netherlands|spain|poland|singapore|australia|sydney|mexico|brazil|emea|apac|latam|europe)\b"
)
# Cities that also exist in the US (Dublin OH/CA, London KY, Vancouver WA, Paris TX, Berlin NH, Amsterdam NY).
_FOREIGN_TWIN = re.compile(r"(?i)\b(?:dublin|london|vancouver|paris|berlin|amsterdam)\b")
_US_STRONG = re.compile(
    r"(?i)\b(?:united states|u\.s\.a?\.?|usa|us[- ]remote|remote[- ,(]*(?:us|usa|united states)|" + _STATES + r")\b|\b\d{5}(?:-\d{4})?\b"
)
# A trailing two-letter US state. DE and IN are left out: in job feeds they almost always mean Germany and India.
_US_STATE_SUFFIX = re.compile(r",\s*(?:" + "|".join(a for a in _ABBR.split("|") if a not in {"DE", "IN"}) + r")\b")


def is_us_location(location: str) -> bool:
    """True for a US city, state, hub or US-remote location.

    Dublin, OH and Vancouver, WA are American; Dublin, Ireland, Vancouver, BC and
    Bengaluru, IN are not. A posting that offers the US among other countries counts.
    """
    location = location or ""
    if not US_LOCATION.search(location):
        return False
    if _US_STRONG.search(location):
        return True
    if _FOREIGN_STRONG.search(location):
        return False
    return not _FOREIGN_TWIN.search(location) or bool(_US_STATE_SUFFIX.search(location))
SENIORITY_BLOCK = re.compile(r"\b(senior|sr\.?|staff|lead|principal|director|head(?: of)?|manager|architect|distinguished|fellow|vp|vice president)\b", re.I)
# A PhD that is merely preferred, or one option among degrees ("MS or PhD"),
# is not a hard requirement.
_PHD = r"ph\.?\s?d\.?"
_PHD_TITLE = re.compile(r"\b" + _PHD + r"\b", re.I)
_PHD_REQUIRED = re.compile(
    r"\b" + _PHD + r"\b[^.\n]{0,30}\b(is\s+)?required\b|\bmust\s+(have|hold)\s+(a\s+)?" + _PHD + r"\b"
    r"|\b(currently\s+)?(pursuing|enrolled\s+in)\s+(a\s+)?" + _PHD + r"\b|\b" + _PHD + r"\s+(candidates?|students?)\s+only\b",
    re.I,
)
_PHD_ONLY_BULLET = re.compile(r"^\s*(?:[-*•·]\s*)?(?:a\s+|an?\s+)?" + _PHD + r"\b(?:\s+degree)?\s+(?:in|from)\b", re.I | re.M)
_OTHER_DEGREE = re.compile(r"\b(m\.?s\.?c?|master'?s?|b\.?s\.?c?|bachelor'?s?|b\.?e\.?|m\.?eng|equivalent)\b", re.I)


def requires_phd(title: str, description: str) -> bool:
    """True when the posting is only for PhD holders or PhD students."""
    if _PHD_TITLE.search(title or ""):
        return True
    text = description or ""
    if _PHD_REQUIRED.search(text):
        return True
    for match in _PHD_ONLY_BULLET.finditer(text):
        line = text[match.start(): text.find("\n", match.start()) if text.find("\n", match.start()) != -1 else len(text)]
        if not _OTHER_DEGREE.search(line) and not re.search(r"\b(preferred|plus|nice to have|bonus)\b", line, re.I):
            return True
    return False
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs)\b(?:\s+of)?\s+(?:\w+\s+){0,4}?(?:experience|exp)", re.I)


def years_required(description: str) -> int | None:
    """Smallest years figure a JD asks for, or None. Sentences about the company's age are ignored."""
    found = []
    for match in _YEARS.finditer(description or ""):
        window = (description or "")[max(0, match.start() - 25): match.start()].casefold()
        after = (description or "")[match.end(): match.end() + 12].casefold()
        if re.search(r"\b(founded|since|over the (last|past)|for the (last|past))\b", window) or after.lstrip().startswith("ago"):
            continue
        found.append(int(match[1]))
    return min(found) if found else None


def _load_profile(root=None) -> dict:
    try:
        import yaml
        from backend.paths import CONFIG
        path = (Path(root) / "data/config/profile.yml") if root else (CONFIG / "profile.yml")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - config missing in a bare test workspace
        return {}


def _track_regexes(profile: dict | None = None, extra_excluded: list | None = None):
    """The title matcher for profile.yml's target roles, role_tracks signals and related titles.

    ``search(title)`` is truthy when a title names a target role in any common wording
    (backend/role_titles.py). ``target_roles.related_titles`` adds titles the candidate
    chose; ``target_roles.excluded_titles`` (and portals.yml exclude_title_words) removes some.
    """
    from backend.role_titles import RoleMatcher, related_titles

    profile = _load_profile() if profile is None else profile
    targets = profile.get("target_roles", {}) or {}
    titles = [str(t) for group in ("primary", "secondary") for t in targets.get(group, []) or []]
    signals = [str(sig) for track in profile.get("role_tracks", []) or [] for sig in track.get("signals", [])]
    # An unfinished profile has no role constraint. Never inject another person's
    # target families into a new workspace.
    roles = list(dict.fromkeys(titles + signals))
    related = [str(t) for t in targets.get("related_titles") or []]
    if roles and targets.get("expand_related", True):
        related += [t for t in related_titles(roles) if t not in related]
    # Built-in related titles the candidate asked the search to drop.
    suppressed = {str(t).casefold() for t in targets.get("suppressed_related") or []}
    related = [t for t in related if t.casefold() not in suppressed]
    excluded = [str(t) for t in (targets.get("excluded_titles") or []) + list(extra_excluded or [])]
    return RoleMatcher(roles, related, excluded)


# Compatibility export; profile-aware callers use ProfileRules.of(root).
SUPPORTED_ROLES = _track_regexes()


def _excluded_title_words(portals: Path) -> list:
    """portals.yml filters.exclude_title_words: titles this profile never pursues."""
    try:
        import yaml
        data = yaml.safe_load(portals.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a missing file excludes nothing
        return []
    return [str(w) for w in ((data.get("filters") or {}).get("exclude_title_words") or []) if str(w).strip()]


class ProfileRules:
    """What one profile's screens read from its own profile.yml, re-read when the file changes."""

    _cache: dict[str, tuple[int, "ProfileRules"]] = {}

    def __init__(self, profile: dict, excluded_titles: list | None = None):
        self.roles = _track_regexes(profile, excluded_titles)
        targets = profile.get("target_roles") or {}
        self.max_years = int(targets.get("max_years_required") or 0)
        scoring = profile.get("scoring") or {}
        self.degree = str(scoring.get("highest_degree") or "unknown qualifications")
        self.block_seniority = bool(scoring.get("block_seniority", False))
        self.phd_known_missing = bool(scoring.get("highest_degree")) and not bool(
            re.search(r"\b(?:ph\.?d\.?|doctorate|doctoral|doctor of philosophy)\b", self.degree, re.I)
        )
        self.name = str((profile.get("candidate") or {}).get("preferred_name") or "").strip() or "The candidate"
        self.role_blocker = str(
            scoring.get("role_blocker")
            or "Role does not match this profile's target roles."
        )

    @classmethod
    def of(cls, root) -> "ProfileRules":
        path = Path(root) / "data/config/profile.yml"
        portals = Path(root) / "data/config/portals.yml"
        stamps = []
        for file in (path, portals):
            try:
                stamps.append(file.stat().st_mtime_ns)
            except OSError:
                stamps.append(0)
        stamp = tuple(stamps)
        cached = cls._cache.get(str(path))
        if not cached or cached[0] != stamp:
            cached = (stamp, cls(_load_profile(root), _excluded_title_words(portals)))
            cls._cache[str(path)] = cached
        return cached[1]
CLOSED = re.compile(
    r"\b(job|position|role|vacancy|requisition|posting)\b.{0,55}\b(closed|expired|filled|no longer available|not accepting applications)\b|\bno longer accepting applications\b",
    re.I | re.S,
)
BLOCKED_PAGE = re.compile(r"captcha|verify you are human|access denied|sign in to continue|log in to continue", re.I)
FRAUD = re.compile(
    r"pay(?:ment)? (?:a |the )?(?:fee|deposit)|gift card|crypto(?:currency)? payment|send (?:your )?(?:passport|identity document)|telegram|whatsapp only|personal email address",
    re.I,
)
def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def company_id(name: str) -> str:
    return "company-" + hashlib.sha256(normalize_company(name).encode()).hexdigest()[:16]


def _host_matches_company(url: str, company: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    if any(host == ats or host.endswith("." + ats) for ats in ATS_DOMAINS | EMPLOYER_BOARDS):
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", company.casefold()) if len(w) > 2]
    return bool(words) and any(word in host.replace("-", "") for word in words)


class JobQualityService:
    def __init__(self, services, fetcher=None):
        self.s = services
        self.w = services.w
        self.fetcher = fetcher or self._fetch

    @staticmethod
    def _fetch(url: str) -> dict:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; CareerDashboardVerifier/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            with urlopen(request, timeout=12) as response:
                body = response.read(750_000).decode(
                    response.headers.get_content_charset() or "utf-8", "replace"
                )
                text = unescape(re.sub(r"<[^>]+>", " ", body))
                return {
                    "status": response.status,
                    "final_url": response.geturl(),
                    "text": re.sub(r"\s+", " ", text)[:250_000],
                }
        except HTTPError as exc:
            return {"status": exc.code, "final_url": exc.geturl(), "text": ""}
        except (URLError, TimeoutError, socket.timeout) as exc:
            return {"status": None, "final_url": url, "text": "", "error": type(exc).__name__}

    def ensure_company(self, name: str) -> str:
        cid = company_id(name)
        with self.w.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO companies(
                id,normalized_name,display_name,created_at,updated_at)
                VALUES(?,?,?,?,?)""",
                (cid, normalize_company(name), name.strip(), utcnow(), utcnow()),
            )
        return cid

    def verify_posting(self, job_id: str, response: dict | None = None) -> dict:
        job = self.w.get_job(job_id)
        if job.get("record_source") == "gmail":
            raise ValueError("This record has no saved public posting to verify")
        result = response or self.fetcher(job["url"])
        status = result.get("status")
        final_url = result.get("final_url") or job["url"]
        text = str(result.get("text") or "")
        evidence: list[str] = []
        state = "active"
        if status in {404, 410}:
            state = "expired"
            evidence.append(f"Posting returned HTTP {status}.")
        elif status is None:
            state = "needs_review"
            evidence.append(f"Posting could not be checked ({result.get('error', 'network error')}).")
        elif BLOCKED_PAGE.search(text) or status in {401, 403, 429}:
            state = "needs_review"
            evidence.append("The site blocked or gated automated verification.")
        elif CLOSED.search(text):
            state = "expired"
            evidence.append("The retrieved page explicitly says the posting is closed or unavailable.")
        else:
            original = urlsplit(job["url"])
            final = urlsplit(final_url)
            original_parts = [p for p in original.path.split("/") if p]
            generic_redirect = (
                final_url.rstrip("/") != job["url"].rstrip("/")
                and len([p for p in final.path.split("/") if p]) <= 1
                and len(original_parts) >= 2
                and job["title"].casefold() not in text.casefold()
            )
            if generic_redirect:
                state = "expired"
                evidence.append("The exact posting redirects to a generic portal and the role is absent.")
            else:
                evidence.append(f"Posting returned HTTP {status or 200} without closure evidence.")
        # Re-gate the live wording: a posting edited to refuse sponsorship leaves the list for Excluded roles.
        refusal = None
        if state == "active" and text and job["status"] in {"saved", "prepared"}:
            from backend.services import sponsorship
            verdict = self.s.gate(job["company"], text, job["url"], job.get("location", ""),
                                  market=job.get("market") or "")
            if verdict.excluded and not sponsorship.overridden(job.get("sponsor_evidence"), verdict):
                from backend.services.demo import demo_mode
                if demo_mode(self.s):
                    evidence.append(f'Demo mode: the posting now says: "{verdict.screen.sentence}" The role stays on the list.')
                else:
                    refusal = verdict
                    evidence.append(f'The posting now says: "{verdict.screen.sentence}" It moved to Excluded roles.')
        checked = utcnow()
        closed_at = checked if state == "expired" else None
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO posting_checks(job_id,state,http_status,final_url,evidence,checked_at) VALUES(?,?,?,?,?,?)",
                (job_id, state, status, final_url, json.dumps(evidence), checked),
            )
            db.execute(
                "UPDATE jobs SET posting_state=?,last_verified_at=?,closed_at=CASE WHEN ?='expired' THEN ? ELSE NULL END,updated_at=? WHERE id=?",
                (state, checked, state, closed_at, checked, job_id),
            )
            self.w.record_event(
                db, "posting_verified", job_id, state=state, evidence=evidence, final_url=final_url
            )
        if refusal:
            self.s.record_excluded({**job, "description": text}, refusal, source="sweep")
            self.w.remove_job(job_id, "Sponsorship gate: the posting now says: " + refusal.screen.sentence)
        self.s.sync_projections()
        return {"job_id": job_id, "state": state, "checked_at": checked, "final_url": final_url, "evidence": evidence,
                "excluded": bool(refusal)}

    def verify_due(self, hours: int = 24, expired_hours: int = 168) -> dict:
        """Re-check open postings, and expired ones far less often.

        Reads the jobs table directly rather than Workspace.jobs(), which already
        filters expired saved/prepared roles out of the active list: going through
        it meant a posting could never be re-checked once marked expired, so a
        wrongly-closed or reposted role could not recover.
        """
        now = datetime.now(timezone.utc)
        fresh = now - timedelta(hours=hours)
        stale = now - timedelta(hours=expired_hours)
        with self.w.connect() as db:
            rows = db.execute(
                "SELECT id, posting_state, last_verified_at FROM jobs "
                "WHERE deleted_at IS NULL AND status IN ('saved','prepared') "
                "AND COALESCE(record_source,'') <> 'gmail'"
            ).fetchall()
        due = []
        for row in rows:
            checked = row["last_verified_at"]
            threshold = stale if row["posting_state"] == "expired" else fresh
            if not checked or datetime.fromisoformat(checked) <= threshold:
                due.append(row["id"])
        results = [self.verify_posting(job_id) for job_id in due]
        return {"checked": len(results), "results": results}

    def posting_history(self, job_id: str) -> list[dict]:
        with self.w.connect() as db:
            rows = db.execute(
                "SELECT * FROM posting_checks WHERE job_id=? ORDER BY checked_at DESC", (job_id,)
            ).fetchall()
        return [{**dict(row), "evidence": json.loads(row["evidence"])} for row in rows]

    def blockers(self, posting: dict) -> list[str]:
        """The hard gates that need no AI: place, seniority, role family, years, PhD, a real posting, sponsorship."""
        title = str(posting.get("title", ""))
        location = str(posting.get("location", ""))
        description = str(posting.get("description", ""))
        blockers = []
        # This profile's own country, targets and wording (profile.yml + its country pack).
        from backend.countries import market_for_location, pack_for
        market = market_for_location(self.w.root, location, requested=posting.get("market"))
        pack = pack_for(self.w.root, market)
        rules = ProfileRules.of(self.w.root)
        # A bare "Remote" is not a refusal; it earns no location points and gets checked at research time.
        if not pack.location_ok(location) and not pack.open_remote(location):
            blockers.append(pack.text("location_blocker"))
        if rules.block_seniority and SENIORITY_BLOCK.search(title):
            blockers.append("Seniority in the title is outside this profile's configured target level.")
        if not rules.roles.search(title):
            blockers.append(rules.role_blocker)
        years = years_required(description)
        if years and rules.max_years and years > rules.max_years:
            blockers.append(f"The posting asks for {years}+ years of experience; the profile caps at {rules.max_years}.")
        if rules.phd_known_missing and requires_phd(title, description):
            blockers.append(f"The posting requires a PhD; {rules.name} holds {rules.degree}.")
        if len(description.strip()) < 80 or not str(posting.get("url", "")).startswith(("http://", "https://")):
            blockers.append("A full job description and real application route are required.")
        from backend.services.sponsorship import rules_for, screen as sponsorship_screen
        gate = sponsorship_screen(title + "\n" + description, rules_for(self.w.root, market))
        if gate.verdict == "EXCLUDED":
            blockers.append(f"Sponsorship gate: {gate.reason_label}. Posting says: \"{gate.sentence}\"")
        return blockers

    def relevance(self, posting: dict, profile_text: str = "", analysis: dict | None = None, cat: dict | None = None) -> dict:
        """Hard gates first, then the fit from the job's verified requirement matrix (services/fit.py).

        ``analysis`` is a matrix already made for this posting (by AI on a free plan); without
        one the rules path scores it from the posting's own words. ``profile_text`` is extra
        registered profile wording the rules path may count as evidence.
        """
        from backend.services import fit
        from backend.services.sponsorship import rules_for, screen as sponsorship_screen

        blockers = self.blockers(posting)
        analysis = analysis or fit.analyse(self.s, posting, cat=cat, profile_text=profile_text)
        for blocker in analysis["matrix"]["hard_blockers"]:
            blockers.append(f"{blocker['reason'][:1].upper() + blocker['reason'][1:]}. Posting says: \"{blocker['excerpt'][:200]}\"")
        score = analysis["score"]
        eligible = not blockers and score >= fit.FIT_THRESHOLD and analysis["must_have_ok"]
        why = "; ".join(blockers)
        if not why and not eligible:
            must = analysis["parts"]["required"]
            why = (f"Meets only {must['met']} of {must['total']} must-haves" if not analysis["must_have_ok"]
                   else f"Fit {score}/100 is below {fit.FIT_THRESHOLD}") + " (" + fit.brief(analysis) + ")."
        from backend.countries import market_for_location
        market = market_for_location(self.w.root, str(posting.get("location", "")), requested=posting.get("market"))
        gate = sponsorship_screen(str(posting.get("title", "")) + "\n" + str(posting.get("description", "")),
                                  rules_for(self.w.root, market))
        return {
            "score": score,
            "eligible": eligible,
            "components": analysis["components"],
            "blockers": blockers,
            "why": why,
            "sponsorship": {"verdict": gate.verdict, "reason": gate.reason, "sentence": gate.sentence},
            "threshold": fit.FIT_THRESHOLD,
            "fit": analysis,
        }

    def assess_company(self, company: str, posting_url: str, sources: list[dict], findings: list[str], red_flags: list[str], *, size_category="unknown", employee_min=None, employee_max=None, sponsorship_state="unknown", override_reason="") -> dict:
        cid = self.ensure_company(company)
        source_urls = [str(source.get("url", "")) for source in sources if source.get("url")]
        owned_or_ats = _host_matches_company(posting_url, company)
        # A cited registry or company-page URL is the record itself, so sources count as much as notes.
        legal_terms = ("company register", "secretary of state", "sec.gov", "edgar", "opencorporates",
                       "legal entity", "trading presence", "registered", "incorporated", "bbb.org",
                       "linkedin.com/company", "crunchbase", "uscis h-1b employer data hub")
        cited_record = any(term in url.casefold() for url in source_urls for term in legal_terms)
        positive_finding = any(
            any(term in finding.casefold() for term in legal_terms)
            and not re.search(
                r"\b(?:no|not|unknown|unverified|unable to|could not|did not|without)\b"
                r".{0,35}\b(?:legal presence|legal entity|registered|incorporated|company register|trading presence)\b",
                finding, re.I,
            )
            for finding in findings
        )
        legal_presence = cited_record or positive_finding
        # Discovery models often put ordinary job-search caveats in red_flags
        # (remote location unclear, a client-facing seat, an old requisition).
        # Preserve those observations without calling a real employer fraud.
        # Only concrete payment, identity-document or off-channel requests are
        # hard legitimacy blocks. Posting completeness and market eligibility
        # have their own gates before this company check.
        detected = [str(flag) for flag in red_flags if FRAUD.search(str(flag))]
        if FRAUD.search(" ".join(findings)):
            detected.append("Payment, identity-document or off-channel contact warning detected.")
        caveats = [str(flag) for flag in red_flags if str(flag) not in detected]
        recorded_findings = [*findings, *("Discovery caveat: " + flag for flag in caveats)]
        if detected:
            state = "blocked"
        elif owned_or_ats and legal_presence and source_urls:
            state = "verified"
        else:
            state = "needs_review"
        if override_reason and state == "needs_review":
            state = "verified"
        checked = utcnow()
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE companies SET website_domain=?,size_category=?,employee_min=?,employee_max=?,
                legitimacy_state=?,sponsorship_state=?,manual_override_reason=?,updated_at=? WHERE id=?""",
                (urlsplit(posting_url).hostname or "", size_category, employee_min, employee_max, state, sponsorship_state, override_reason, checked, cid),
            )
            db.execute(
                "INSERT INTO company_checks(company_id,state,sources,findings,red_flags,checked_at,manual_override,override_reason) VALUES(?,?,?,?,?,?,?,?)",
                (cid, state, json.dumps(sources), json.dumps(recorded_findings), json.dumps(detected), checked, int(bool(override_reason)), override_reason),
            )
            db.execute("UPDATE jobs SET company_id=? WHERE lower(replace(company,' ',''))=?", (cid, normalize_company(company)))
            self.w.record_event(db, "company_verified", company_id=cid, company=company, state=state, sources=source_urls)
        self.s.sync_projections()
        return {"company_id": cid, "state": state, "sources": sources, "findings": recorded_findings,
                "red_flags": detected, "caveats": caveats, "checked_at": checked}

    def balanced_five(self, candidates: list[dict], total: int = 5) -> dict:
        """Return an honest 2 startup / 1 mid / 2 large subset, scaled to ``total`` jobs. Mid and large companies must be tier S/A/B (cap-exempt, says yes, or proven sponsor); startups may be tier C.

        That sponsor-record rule needs a sponsor history to check against (the US pack's
        USCIS index). A country without one (Ireland) has no proven-sponsor tier, so there
        the gate's own verdict is the rule: a posting that refuses a permit is already out."""
        from backend.countries import pack_for

        primary = pack_for(self.w.root).code
        valid = [
            item for item in candidates
            if item.get("legitimacy_state") == "verified" and item.get("relevance", {}).get("eligible")
        ]
        selected = []
        shortages = []
        startups, mids = round(total * 2 / 5), round(total / 5)
        for category, count in (
            ("startup", startups), ("mid", mids), ("large", total - startups - mids)
        ):
            if count <= 0:
                continue
            pool = [
                item for item in valid
                if item.get("size_category") == category
                and (category == "startup"
                     or not pack_for(self.w.root, item.get("market") or primary).sponsor_index
                     or item.get("sponsor_tier") in {"S", "A", "B"})
            ]
            if category == "startup":
                pool = [item for item in pool if self._low_competition(item)]
            selected.extend(pool[:count])
            if len(pool) < count:
                shortages.append({"category": category, "needed": count, "found": len(pool)})
        return {"preset": "balanced_five", "jobs": selected, "complete": not shortages, "shortages": shortages}

    @staticmethod
    def _low_competition(item: dict) -> bool:
        applicants = item.get("applicant_count")
        if isinstance(applicants, int):
            return applicants < 10
        signals = item.get("competition_signals", {})
        count = sum(bool(signals.get(key)) for key in ("posted_within_72h", "limited_syndication", "niche_match"))
        return count >= 2
