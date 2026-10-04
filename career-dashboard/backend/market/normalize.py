"""Plain facts about one public posting: county, kind, level, role family and closing date.

Everything is read from the posting's own fields and words; what a posting does not say stays
unknown (an empty string or None). Nothing here judges a candidate or a permit.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

COUNTIES = ("Carlow", "Cavan", "Clare", "Cork", "Donegal", "Dublin", "Galway", "Kerry", "Kildare", "Kilkenny",
            "Laois", "Leitrim", "Limerick", "Longford", "Louth", "Mayo", "Meath", "Monaghan", "Offaly",
            "Roscommon", "Sligo", "Tipperary", "Waterford", "Westmeath", "Wexford", "Wicklow")
# Places postings name more often than their county.
TOWNS = {
    "Arklow": "Wicklow", "Ashbourne": "Meath", "Athlone": "Westmeath", "Balbriggan": "Dublin", "Ballina": "Mayo",
    "Ballinasloe": "Galway", "Blanchardstown": "Dublin", "Bray": "Wicklow", "Carrick-on-Shannon": "Leitrim",
    "Carrigtwohill": "Cork", "Castlebar": "Mayo", "Celbridge": "Kildare", "Cherrywood": "Dublin", "Citywest": "Dublin",
    "Clondalkin": "Dublin", "Clonee": "Meath", "Clonmel": "Tipperary", "Cobh": "Cork", "Drogheda": "Louth",
    "Dun Laoghaire": "Dublin", "Dundalk": "Louth", "Dungarvan": "Waterford", "Ennis": "Clare", "Enniscorthy": "Wexford",
    "Gorey": "Wexford", "Grangecastle": "Dublin", "Greystones": "Wicklow", "Kells": "Meath", "Killarney": "Kerry",
    "Kinsale": "Cork", "Leixlip": "Kildare", "Letterkenny": "Donegal", "Little Island": "Cork", "Lucan": "Dublin",
    "Mallow": "Cork", "Maynooth": "Kildare", "Midleton": "Cork", "Mullingar": "Westmeath", "Naas": "Kildare",
    "Navan": "Meath", "Nenagh": "Tipperary", "Newbridge": "Kildare", "Oranmore": "Galway", "Portlaoise": "Laois",
    "Ringaskiddy": "Cork", "Sandyford": "Dublin", "Shannon": "Clare", "Swords": "Dublin", "Tallaght": "Dublin",
    "Thurles": "Tipperary", "Tralee": "Kerry", "Tramore": "Waterford", "Trim": "Meath", "Tullamore": "Offaly",
    "Youghal": "Cork",
}
# EU NUTS regions (2021 classification) that EURES gives instead of a county.
NUTS = {
    "IE04": ("Northern and Western", ("Cavan", "Donegal", "Galway", "Leitrim", "Mayo", "Monaghan", "Roscommon", "Sligo")),
    "IE041": ("Border", ("Cavan", "Donegal", "Leitrim", "Monaghan", "Sligo")),
    "IE042": ("West", ("Galway", "Mayo", "Roscommon")),
    "IE05": ("Southern", ("Carlow", "Clare", "Cork", "Kerry", "Kilkenny", "Limerick", "Tipperary", "Waterford", "Wexford")),
    "IE051": ("Mid-West", ("Clare", "Limerick", "Tipperary")),
    "IE052": ("South-East", ("Carlow", "Kilkenny", "Waterford", "Wexford")),
    "IE053": ("South-West", ("Cork", "Kerry")),
    "IE06": ("Eastern and Midland", ("Dublin", "Kildare", "Laois", "Longford", "Louth", "Meath", "Offaly", "Westmeath", "Wicklow")),
    "IE061": ("Dublin", ("Dublin",)),
    "IE062": ("Mid-East", ("Kildare", "Louth", "Meath", "Wicklow")),
    "IE063": ("Midland", ("Laois", "Longford", "Offaly", "Westmeath")),
}
_PLACE = re.compile(r"(?i)(?<![a-z])(" + "|".join(re.escape(name) for name in sorted({*COUNTIES, *TOWNS}, key=len, reverse=True))
                    + r")(?![a-z])")
_NORTHERN = re.compile(r"(?i)northern ireland|\b(belfast|derry|londonderry|co\.?\s+(antrim|armagh|down|fermanagh|tyrone))\b")


def counties(location: str = "", nuts: list[str] | None = None) -> list[str]:
    """The Irish counties a posting's location names (a NUTS region settles one only for Dublin).

    Only the location field is read: a description names people and suppliers too ("Clare",
    "Kerry"). Northern Ireland places are not Republic counties.
    """
    text = _NORTHERN.sub(" ", str(location or ""))
    found = []
    for match in _PLACE.finditer(text):
        name = next((c for c in COUNTIES if c.casefold() == match[1].casefold()), None) or TOWNS.get(
            next((t for t in TOWNS if t.casefold() == match[1].casefold()), ""), "")
        if name:
            found.append(name)
    if not found:
        for code in nuts or []:
            _, members = NUTS.get(str(code).upper(), ("", ()))
            if len(members) == 1:
                found.append(members[0])
    return list(dict.fromkeys(found))


def region(nuts: list[str] | None) -> str:
    """'Mid-East' for IE062: the EURES region in words, or ''."""
    names = [NUTS[str(code).upper()][0] for code in nuts or [] if str(code).upper() in NUTS]
    return ", ".join(dict.fromkeys(names))


_INTERN = re.compile(r"(?i)\b(intern(ship)?|placement|co-?op|summer student|work experience)\b")
_PROGRAMME = re.compile(r"(?i)\b(graduate|grad)\b.{0,40}\b(programme|program|scheme|rotation|intake|development)\b"
                        r"|\b(programme|program|scheme)\b.{0,20}\bgraduates?\b")
_ENTRY = re.compile(r"(?i)\b(graduate|grad|junior|jr\.?|entry[- ]level|trainee|apprentice|new grad|early careers?)\b")
_SENIOR = re.compile(r"(?i)\b(senior|sr\.?|staff|lead|principal|director|head(?: of)?|manager|architect|vp|vice president|chief)\b")


def years_required(description: str) -> int | None:
    from backend.job_quality import years_required as smallest

    return smallest(description or "")


def posting_type(title: str, description: str = "") -> str:
    """graduate_programme, internship, entry, experienced or unspecified."""
    title = str(title or "")
    if _INTERN.search(title):
        return "internship"
    if _PROGRAMME.search(title) or (re.search(r"(?i)\bgraduate\b", title) and _PROGRAMME.search(str(description or "")[:3000])):
        return "graduate_programme"
    years = years_required(description)
    if _ENTRY.search(title) or (years is not None and years <= 2 and not _SENIOR.search(title)):
        return "entry"
    if _SENIOR.search(title) or (years is not None and years >= 3):
        return "experienced"
    return "unspecified"


def level(title: str, description: str = "") -> str:
    """entry, mid or senior from the title and the years asked for; '' when the posting says neither."""
    years = years_required(description)
    if _ENTRY.search(str(title or "")) or _INTERN.search(str(title or "")):
        return "entry"
    if _SENIOR.search(str(title or "")) or (years is not None and years >= 6):
        return "senior"
    if years is not None:
        return "entry" if years <= 2 else "mid"
    return ""


# The first family whose pattern names the title wins; titles with none stay unclassified.
ROLE_FAMILIES = (
    ("data_engineering", r"\bdata engineer|\betl developer|\bbig data engineer|\banalytics engineer"),
    ("data_science", r"\bdata scien|\bmachine learning|\bml engineer|\bai engineer|\bnlp engineer|\bcomputer vision"),
    ("data_analytics", r"\bdata analyst|\b(bi|business intelligence|reporting|insights?|analytics) (analyst|developer|specialist)|\banalyst,? data\b"),
    ("devops_cloud", r"\bdevops|\bsite reliability|\bsre\b|\bcloud (engineer|architect|developer)|\bplatform engineer|\binfrastructure engineer"),
    ("cybersecurity", r"\b(security|cyber ?security|soc|information security) (analyst|engineer|specialist)|\bpenetration tester"),
    ("qa_testing", r"\b(qa|quality assurance|test automation|automation test|software test)\w* (engineer|analyst|tester|developer)|\btester\b"),
    ("software_engineering", r"\b(software|backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|web|mobile|ios|android|java|python|\.net|c\+\+|application|embedded|firmware|game)\s+(engineer|developer|programmer)|\bsoftware developer|\bdeveloper\b|\bprogrammer\b"),
    ("it_support", r"\b(it|technical|desktop|service desk|help ?desk|ict)\s+(support|technician|analyst)|\bsystems administrator"),
    ("business_analysis", r"\b(business|business systems|systems|product|functional) analyst"),
    ("product_project", r"\bproduct (manager|owner)|\bproject (manager|coordinator|analyst)|\bprogramme (manager|coordinator)|\bscrum master|\bpmo\b"),
    ("finance_accounting", r"\baccountant|\baccounts (assistant|payable|receivable)|\bfinancial analyst|\bfinance (analyst|associate|assistant)|\baudit|\btax (associate|analyst|consultant)|\bpayroll|\bfund (accountant|administrator|accounting)|\btreasury|\bactuar|\bcredit analyst"),
    ("marketing", r"\bmarketing|\bseo\b|\bcontent (writer|executive|marketer)|\bsocial media|\bbrand (executive|manager)|\bdigital marketing"),
    ("sales_business_development", r"\bsales\b|\bbusiness development|\baccount (executive|manager)|\bbdr\b|\bsdr\b"),
    ("customer_support", r"\bcustomer (support|service|success|care|experience)|\bcall cent(re|er)|\bcontact cent(re|er)"),
    ("hr_recruitment", r"\b(hr|human resources|people|talent)\s+(advisor|partner|coordinator|specialist|generalist|associate|assistant|acquisition)|\brecruit"),
    ("mechanical_engineering", r"\bmechanical (design )?engineer"),
    ("electrical_engineering", r"\belectrical (design )?engineer|\belectronics? engineer"),
    ("civil_engineering", r"\bcivil engineer|\bstructural engineer|\bsite engineer|\bquantity surveyor"),
    ("process_manufacturing", r"\b(process|manufacturing|quality|validation|production|automation|chemical) engineer|\bcqv\b"),
    ("science_laboratory", r"\b(lab|laboratory|qc|quality control) (analyst|technician|scientist)|\bscientist\b|\bchemist\b|\bmicrobiolog"),
    ("healthcare", r"\bnurse\b|\bnursing\b|\bmidwi|\b(care|healthcare|health care) assistant|\bphysiotherap|\bpharmacist|\bradiograph|\bdoctor\b|\bregistrar\b"),
    ("supply_chain_logistics", r"\bsupply chain|\blogistics|\bprocurement|\bbuyer\b|\b(demand|supply|material) planner|\bwarehouse"),
    ("design_ux", r"\b(ux|ui|product|graphic|visual|interaction) designer|\buser experience"),
    ("hospitality", r"\bchef\b|\bcook\b|\bkitchen|\bwaiter|\bbarista|\bhotel|\breceptionist"),
)
_FAMILIES = [(name, re.compile(pattern, re.I)) for name, pattern in ROLE_FAMILIES]


def role_family(title: str) -> str:
    """The broad kind of work a title names ('data_analytics'), or ''."""
    for name, pattern in _FAMILIES:
        if pattern.search(str(title or "")):
            return name
    return ""


def title_key(title: str) -> str:
    """A title without punctuation, levels in brackets or requisition codes, for spotting the same role twice."""
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]|\b(req|job|ref)[ #:]*[\w-]*\d[\w-]*", " ", str(title or ""), flags=re.I)
    return " ".join(re.findall(r"[a-z0-9+#]+", text.casefold()))


_MONTHS = {m: n for n, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_CLOSING = re.compile(r"(?i)\b(closing date|closes?(?: on)?|closing on|deadline(?: for applications)?|apply by|applications close(?: on)?)"
                      r"\s*[:\-]?\s*(?:on\s+)?(?:[A-Za-z]+day,?\s+)?"
                      r"(\d{1,2})(?:st|nd|rd|th)?[\s/.\-]+([A-Za-z]{3,9}|\d{1,2})[\s/.\-,]+(\d{4})")


def closing_date(description: str = "", valid_through: str = "") -> str:
    """The closing date a posting states (schema.org validThrough first), as YYYY-MM-DD, or ''.

    Irish postings write day before month (30/10/2026).
    """
    stamp = str(valid_through or "").strip()
    if stamp:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")[:25]).date().isoformat()
        except ValueError:
            try:
                return date.fromisoformat(stamp[:10]).isoformat()
            except ValueError:
                pass
    match = _CLOSING.search(str(description or ""))
    if not match:
        return ""
    day, month, year = int(match[2]), match[3], int(match[4])
    number = int(month) if month.isdigit() else _MONTHS.get(month[:3].casefold())
    try:
        return date(year, number, day).isoformat() if number else ""
    except ValueError:
        return ""


def epoch_ms(value) -> str:
    """An epoch-milliseconds timestamp (EURES) as an ISO UTC date-time, or ''."""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
