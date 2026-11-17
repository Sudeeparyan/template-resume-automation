"""Employer names as matching keys, for DETE's permit lists and job postings.

DETE lists legal entities ("Stripe Payments Europe Limited", "Google Ireland Limited");
postings name brands ("Stripe", "Google") or, on EURES, the same legal names. A key drops
the legal form and a trailing country ("google"); a brand matches a legal entity exactly,
through an alias in countries/ie/employer-aliases.yml, or as a prefix followed only by
corporate descriptor words ("stripe" + "payments europe"), never by an arbitrary word
("citi" never matches "citi bus").

The importer (scripts/import_dete_permits.py) and every lookup use this one module, so the
keys always agree.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import yaml

from backend.paths import COUNTRIES

# Legal forms, removed from the end of a name (repeatedly: "company limited by guarantee").
LEGAL = {
    "limited",
    "ltd",
    "dac",
    "clg",
    "teoranta",
    "teo",
    "plc",
    "llp",
    "lp",
    "uc",
    "ulc",
    "unlimited",
    "company",
    "co",
    "by",
    "guarantee",
    "designated",
    "activity",
    "public",
    "partnership",
    "sarl",
    "bv",
    "nv",
    "se",
    "sa",
    "ag",
    "gmbh",
    "inc",
    "incorporated",
    "corporation",
    "corp",
    "llc",
    "srl",
    "spa",
    "ab",
    "oy",
    "branch",
    "slu",
    "sl",
    "sas",
    "cuideachta",
    "neamhtheoranta",
    "anonim",
    "sirketi",
}
# A trailing country or region, removed after the legal form ("google ireland" -> "google").
TRAILING_PLACE = {"ireland", "irl", "eire", "europe", "emea", "eu", "of", "the"}
# Words that may follow a brand inside a legal name: "stripe payments europe", "google cloud emea".
DESCRIPTORS = {
    "ireland",
    "irl",
    "eire",
    "irish",
    "europe",
    "european",
    "emea",
    "eu",
    "international",
    "global",
    "worldwide",
    "dublin",
    "cork",
    "galway",
    "limerick",
    "waterford",
    "athlone",
    "shannon",
    "kildare",
    "operations",
    "operation",
    "services",
    "service",
    "technology",
    "technologies",
    "computing",
    "tech",
    "payments",
    "platforms",
    "platform",
    "holdings",
    "holding",
    "distribution",
    "development",
    "research",
    "and",
    "r",
    "d",
    "data",
    "cloud",
    "solutions",
    "systems",
    "software",
    "digital",
    "financial",
    "finance",
    "capital",
    "investments",
    "investment",
    "management",
    "group",
    "labs",
    "laboratories",
    "pharmaceuticals",
    "pharmaceutical",
    "pharma",
    "manufacturing",
    "sales",
    "support",
    "centre",
    "center",
    "shared",
    "business",
    "transformation",
    "consulting",
    "advisory",
    "insurance",
    "reinsurance",
    "bank",
    "banking",
    "markets",
    "securities",
    "asset",
    "assets",
    "funds",
    "fund",
    "ventures",
    "energy",
    "health",
    "healthcare",
    "medical",
    "devices",
    "diagnostics",
    "products",
    "retail",
    "online",
    "media",
    "entertainment",
    "games",
    "studios",
    "networks",
    "communications",
    "telecommunications",
    "engineering",
    "automation",
    "analytics",
    "intelligence",
    "ai",
    "commerce",
    "logistics",
    "aviation",
    "leasing",
    "trading",
    "treasury",
    "partners",
    "unit",
    "hub",
    "innovation",
    "innovations",
    "biologics",
    "biotech",
    "sciences",
    "science",
    "life",
    "medtech",
    "semiconductor",
    "semiconductors",
    "hosting",
    "infrastructure",
    "web",
    "eu",
}
# A brand that is one of these words matches only exactly or through an alias.
STOP_BRANDS = {
    "global",
    "first",
    "irish",
    "dublin",
    "national",
    "health",
    "data",
    "tech",
    "medical",
    "care",
    "digital",
    "international",
    "systems",
    "software",
    "consulting",
    "the",
    "european",
    "services",
    "solutions",
    "group",
    "ireland",
    "europe",
    "capital",
    "energy",
    "home",
    "homes",
    "smart",
    "bright",
    "best",
    "new",
    "one",
}
# More than five candidate legal entities is ambiguous; history must not be attributed.
MAX_ENTITIES = 5

_TRADING = re.compile(r"\s(?:t/a|t\.a\.|trading as)\s", re.I)
_PARENS = re.compile(r"\([^)]*\)")


def _plain(text: str) -> str:
    text = (
        unicodedata.normalize("NFKD", str(text or ""))
        .encode("ascii", "ignore")
        .decode()
    )
    text = text.casefold().replace("&", " and ").replace("'", "").replace("’", "")
    text = _PARENS.sub(" ", text)
    text = re.sub(r",?\s*irish branch\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_ie(name: str) -> str:
    """The matching key of one employer name ("" when nothing is left)."""
    tokens = _plain(_TRADING.split(f" {name} ")[0]).split()
    while tokens and tokens[-1] in LEGAL:
        tokens.pop()
    while len(tokens) > 1 and tokens[-1] in TRAILING_PLACE:
        tokens.pop()
        while tokens and tokens[-1] in LEGAL:
            tokens.pop()
    return " ".join(tokens)


def keys_for(name: str) -> list[str]:
    """Every key a name stands for: the legal name and, for "X t/a Y", the trading name Y."""
    parts = [p for p in _TRADING.split(f" {name} ") if p.strip()]
    if not parts:
        return []
    keys = [normalize_ie(parts[0])] + [normalize_ie(p) for p in parts[1:]]
    return [k for k in dict.fromkeys(keys) if k]


# ---- people are not employers to publish ----------------------------------------------------

_BUSINESS_WORDS = re.compile(
    r"(?i)\b(limited|ltd|dac|clg|teoranta|teo|plc|llp|partnership|company|uc|ulc|unlimited|sarl|b\.?v\.?|se|gmbh|inc"
    r"|corporation|branch|trust|society|association|council|hospital|hospice|university|college|institute|school"
    r"|academy|club|centre|center|services|service|group|holdings|nursing|home|homes|hotel|restaurant|cafe|bar|pub"
    r"|farm|farms|stud|construction|builders|care|clinic|pharmacy|chemist|solicitors|accountants|accountancy|motors"
    r"|garage|dental|medical|surgery|practice|veterinary|takeaway|foods|food|kitchen|bakery|butchers|stores|store"
    r"|shop|salon|barbers|beauty|cleaning|consultancy|consulting|engineering|technologies|technology|systems"
    r"|solutions|international|ireland|irish|europe|national|county|health|hse|board|department|authority|church"
    r"|parish|diocese|order|sisters|brothers|congregation|foundation|project|network|agency|estate|stables|kennels"
    r"|fisheries|seafoods|nurseries|plant|hire|transport|haulage|logistics|retail|pharma|labs|laboratories"
    r"|studio|studios|media|digital|productions|recruitment|t/a|trading|healthcare|homecare|homecarer|community"
    r"|mushrooms|meat|meats|processors|convent|building|bakeries|plastic|packaging|communications|utilities"
    r"|commission|products|private|scientific|nui|house|living|independent|cuideachta|neamhtheoranta|slu|anonim"
    r"|sirketi|partners|associates|architects|law|legal|financial|insurance|bank|capital|properties|property"
    r"|developments|interiors|diagnostics|theatre|post|airlines|airways|aviation|pharmaceuticals|sons|bros)\b"
)
_PERSON_TOKEN = re.compile(r"^[a-z][a-z'\-]*$|^[a-z]\.?$")


def is_individual(name: str) -> bool:
    """A private person (a sole trader or a household employing a carer), not a company.

    DETE lists them by their own names; this app never republishes or matches them. A firm
    named like a person ("Grant Thornton") is kept only when employer-aliases.yml lists it
    under ``organisations``; any other person-shaped name without a company word is left out.
    """
    text = " ".join(str(name or "").split())
    legal_name = _TRADING.split(f" {text} ")[0].strip()
    if normalize_ie(legal_name) in organisations():
        return False
    # Publish positively identifiable companies and public/institutional employers
    # only. A business word such as "farm", "clinic", "t/a" or "services" can
    # describe a sole trader, so it cannot turn a private name into public data.
    corporate = re.compile(r"(?i)\b(limited|ltd|dac|clg|teoranta|teo|plc|llp|uc|ulc|unlimited|"
                           r"incorporated|inc|corporation|corp|llc|pllc|gmbh|sarl|b\.?v\.?|n\.?v\.?|"
                           r"srl|spa|cuideachta|neamhtheoranta|designated activity company)\b")
    institutional = re.compile(r"(?i)\b(hospital|university|college|institute|council|authority|"
                               r"department|commission|diocese|congregation|convent|hospice|"
                               r"foundation|association|society|hse)\b|\bhealth service executive\b")
    return not bool(text and (corporate.search(legal_name) or institutional.search(legal_name)))


# ---- brand to legal names ------------------------------------------------------------------


@lru_cache(maxsize=1)
def _alias_file() -> dict:
    try:
        data = (
            yaml.safe_load(
                (COUNTRIES / "ie" / "employer-aliases.yml").read_text(encoding="utf-8")
            )
            or {}
        )
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def organisations() -> frozenset[str]:
    """Keys of firms whose names look like a person's (employer-aliases.yml ``organisations``)."""
    return frozenset(
        normalize_ie(str(name)) for name in _alias_file().get("organisations") or []
    )


@lru_cache(maxsize=1)
def aliases() -> dict[str, list[str]]:
    """countries/ie/employer-aliases.yml as {brand key: [legal keys]}."""
    data = _alias_file()
    out: dict[str, list[str]] = {}
    for brand, legal_names in (data.get("aliases") or {}).items():
        keys = [k for name in legal_names or [] for k in keys_for(str(name))]
        if normalize_ie(str(brand)) and keys:
            out[normalize_ie(str(brand))] = list(dict.fromkeys(keys))
    return out


def prefix_match(brand_key: str, legal_key: str, *, entity_count: int = 1) -> bool:
    """A guarded brand prefix; the caller supplies the number of candidate legal entities.

    The first brand token must have at least four characters. More than five candidate
    entities is ambiguous even when this pair looks plausible.
    """
    if not 1 <= entity_count <= MAX_ENTITIES:
        return False
    if (
        not brand_key
        or legal_key == brand_key
        or not legal_key.startswith(brand_key + " ")
    ):
        return False
    first_token = brand_key.partition(" ")[0]
    if first_token in STOP_BRANDS or len(first_token) < 4:
        return False
    return all(
        token in DESCRIPTORS for token in legal_key[len(brand_key) + 1 :].split()
    )
