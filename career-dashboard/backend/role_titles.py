"""Does a job title name one of the candidate's target roles?

A plain phrase search missed most real titles. "Analyst, Data & Insights", "Junior BI
Developer (Power BI)", "Graduate Programme - Data Analytics" and "ML Engineer" all name
the work of a data analyst, BI developer or machine-learning engineer without the exact
words in order, so the old gate turned them away before any fit check saw them.

The matcher reads each target role as a head noun (analyst, engineer, developer ...) and
its qualifiers (data, BI, machine learning), expands the usual abbreviations both ways,
and accepts a title holding the head (or an equivalent head) and every qualifier of one
alternative, in any order. "BI/Power BI Developer" has two alternatives; "Data Engineer /
Data Scientist" is two roles. Related titles for common role families widen the net the
same way, and the candidate can add or exclude titles in profile.yml. Seniority words
never decide a match: the seniority gate reads them separately, and the requirement
check (services/fit.py) still decides whether a matched job actually fits.
"""

from __future__ import annotations

import re

# Written short in titles, long in others (or the reverse). Both forms count.
ABBREVIATIONS = (
    ("ml", "machine learning"), ("ai", "artificial intelligence"), ("bi", "business intelligence"),
    ("nlp", "natural language processing"), ("genai", "generative ai"), ("llm", "large language model"),
    ("qa", "quality assurance"), ("ux", "user experience"), ("ui", "user interface"),
    ("hr", "human resources"), ("mi", "management information"), ("fp&a", "financial planning and analysis"),
    ("swe", "software engineer"), ("sde", "software development engineer"), ("sre", "site reliability engineer"),
    ("rpa", "robotic process automation"), ("erp", "enterprise resource planning"), ("crm", "customer relationship management"),
)
# A developer and a software engineer do the same job under two names.
HEAD_EQUIVALENTS = {
    "developer": ("engineer", "programmer"),
    "engineer": ("developer",),
    "programmer": ("developer", "engineer"),
    "scientist": ("science",),
    "science": ("scientist",),
    "analyst": ("analytics", "analysis"),
    "analytics": ("analyst",),
}
# Level and filler words: never required, never a qualifier.
IGNORED = {
    "a", "an", "and", "the", "of", "for", "in", "with", "to", "or", "at", "on", "role", "position", "job",
    "graduate", "grad", "junior", "jr", "senior", "sr", "entry", "level", "trainee", "intern", "internship",
    "apprentice", "apprenticeship", "new", "early", "career", "careers", "programme", "program", "i", "ii",
    "iii", "iv", "1", "2", "3", "lead", "principal", "staff", "head", "chief", "mid", "experienced",
    "remote", "hybrid",
}
# Plural and -ing forms written in titles.
_STEMS = {
    "engineering": "engineer", "engineers": "engineer", "developers": "developer", "analysts": "analyst",
    "scientists": "scientist", "consultants": "consultant", "programmers": "programmer", "managers": "manager",
    "designers": "designer", "researchers": "researcher", "specialists": "specialist", "architects": "architect",
    "analytical": "analytics",
}

# Titles employers commonly use for the same work, keyed by a target role with level words
# removed. Conservative on purpose: each entry must describe the same day-to-day job.
RELATED = {
    "data analyst": ["data analytics", "bi analyst", "insights analyst", "reporting analyst", "analytics analyst",
                     "business data analyst", "mi analyst", "data and insights analyst", "analytics associate",
                     "data quality analyst", "product analyst", "marketing analyst"],
    "business intelligence analyst": ["bi analyst", "bi developer", "reporting analyst", "data analyst", "insights analyst"],
    "bi analyst": ["business intelligence analyst", "bi developer", "reporting analyst", "data analyst"],
    "bi developer": ["power bi developer", "business intelligence developer", "bi engineer", "reporting developer",
                     "bi analyst", "power bi analyst"],
    "power bi developer": ["bi developer", "power bi analyst", "business intelligence developer", "reporting developer"],
    "reporting analyst": ["mi analyst", "reporting specialist", "insights analyst", "bi analyst", "data analyst"],
    "insights analyst": ["data analyst", "reporting analyst", "customer insights analyst", "analytics analyst"],
    "analytics consultant": ["data consultant", "data analytics consultant", "bi consultant", "analytics specialist"],
    "business analyst": ["business systems analyst", "it business analyst", "process analyst", "business intelligence analyst"],
    "data scientist": ["machine learning scientist", "applied scientist", "decision scientist", "data science",
                       "ml scientist", "research scientist machine learning"],
    "machine learning engineer": ["ml engineer", "ai engineer", "applied ml engineer", "mlops engineer",
                                  "deep learning engineer", "machine learning developer"],
    "ai engineer": ["machine learning engineer", "genai engineer", "llm engineer", "applied ai engineer",
                    "ai developer", "generative ai engineer"],
    "generative ai engineer": ["genai engineer", "llm engineer", "ai engineer", "ai application developer"],
    "data engineer": ["analytics engineer", "etl developer", "big data engineer", "data platform engineer",
                      "data pipeline engineer", "data integration engineer"],
    "analytics engineer": ["data engineer", "bi engineer", "analytics developer"],
    "software engineer": ["software developer", "backend engineer", "full stack developer", "software development engineer",
                          "application developer", "programmer", "web developer", "platform engineer"],
    "software developer": ["software engineer", "application developer", "full stack developer", "backend developer",
                           "programmer", "web developer"],
    "financial analyst": ["finance analyst", "fp&a analyst", "financial planning analyst", "commercial finance analyst"],
    "quantitative analyst": ["quant analyst", "quant researcher", "quantitative researcher", "quantitative developer"],
    "cloud engineer": ["devops engineer", "platform engineer", "site reliability engineer", "infrastructure engineer"],
    "devops engineer": ["cloud engineer", "site reliability engineer", "platform engineer", "build engineer"],
    "qa engineer": ["test engineer", "software tester", "quality assurance engineer", "automation tester", "sdet"],
    "cybersecurity analyst": ["security analyst", "soc analyst", "information security analyst", "cyber security analyst"],
    "security analyst": ["cybersecurity analyst", "soc analyst", "information security analyst"],
    "product manager": ["product owner", "associate product manager", "technical product manager"],
    "project manager": ["project coordinator", "delivery manager", "programme manager"],
    "ux designer": ["product designer", "ui designer", "user experience designer", "interaction designer"],
    # Beyond tech: the same work under the names Irish and US employers use.
    "customer support specialist": ["customer service representative", "customer support agent", "customer care advisor",
                                    "customer success associate", "technical support specialist", "support specialist"],
    "customer service representative": ["customer support specialist", "customer care advisor", "customer service advisor"],
    "accountant": ["accounts assistant", "staff accountant", "financial accountant", "management accountant",
                   "trainee accountant", "audit associate"],
    "marketing executive": ["marketing assistant", "digital marketing executive", "marketing coordinator",
                            "marketing associate", "content marketing executive"],
    "hr assistant": ["hr administrator", "people operations associate", "hr coordinator", "recruitment coordinator"],
    "recruiter": ["talent acquisition associate", "recruitment consultant", "talent acquisition specialist"],
    "sales representative": ["sales development representative", "business development representative",
                             "account executive", "inside sales representative"],
    "mechanical engineer": ["design engineer", "manufacturing engineer", "process engineer"],
    "process engineer": ["manufacturing engineer", "production engineer", "process development engineer"],
    "quality engineer": ["quality assurance engineer", "validation engineer", "qa engineer"],
    "research assistant": ["research associate", "laboratory technician", "research technician"],
    "laboratory analyst": ["qc analyst", "quality control analyst", "lab technician", "laboratory technician"],
}

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#&]*(?:\.[a-z0-9]+)?")


def tokens(text: str) -> list[str]:
    """Lower-case words in order, with plural and -ing forms folded ("Engineering" -> "engineer")."""
    text = (text or "").casefold().replace("fp&a", "fpanda").replace("&", " and ").replace("fpanda", "fp&a")
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)  # "full-stack" and "full stack" are one title
    out = []
    for word in _TOKEN.findall(text):
        word = word.strip(".")
        out.append(_STEMS.get(word, word))
    return out


def _expanded(words: list[str]) -> set[str]:
    """The words plus both forms of every abbreviation they contain."""
    found = set(words)
    joined = " " + " ".join(words) + " "
    for short, long in ABBREVIATIONS:
        long_words = tokens(long)
        if short in found:
            found.update(long_words)
        if " " + " ".join(long_words) + " " in joined:
            found.add(short)
    return found


def _strip(text: str) -> str:
    return re.sub(r"\([^)]*\)", " ", text or "")


def alternatives(role: str) -> list[tuple[str, frozenset]]:
    """(head, qualifiers) for each way a target role can be written.

    "BI/Power BI Developer" -> [("developer", {"bi"}), ("developer", {"power", "bi"})];
    "Data Engineer / Data Scientist" -> two separate roles.
    """
    out = []
    for part in re.split(r"\s+/\s+|\s*,\s*|\s+or\s+|\s*;\s*|\s*\|\s*", _strip(role)):
        pieces = [p.strip() for p in part.split("/") if p.strip()]
        if not pieces:
            continue
        # A multi-word piece before the last is a whole role ("Data Engineer/Data Scientist");
        # a one-word piece is another qualifier for the shared head ("BI/Power BI Developer").
        roles = [p for p in pieces[:-1] if len(p.split()) > 1] + [pieces[-1]]
        options = [p for p in pieces[:-1] if len(p.split()) == 1]
        for index, whole in enumerate(roles):
            words = [w for w in tokens(whole) if w not in IGNORED]
            if not words:
                continue
            head, quals = words[-1], words[:-1]
            out.append((head, frozenset(quals)))
            if index == len(roles) - 1:
                for option in options:
                    extra = [w for w in tokens(option) if w not in IGNORED]
                    if extra:
                        out.append((head, frozenset(extra)))
    return list(dict.fromkeys(out))


def related_titles(roles: list[str]) -> list[str]:
    """RELATED titles for the target roles, excluding ones already targeted."""
    have = {" ".join(w for w in tokens(_strip(role)) if w not in IGNORED) for role in roles}
    found = []
    for role in roles:
        key = " ".join(w for w in tokens(_strip(role)) if w not in IGNORED)
        for alt in [key] + [" ".join([*sorted(q), h]) for h, q in alternatives(role)]:
            for title in RELATED.get(alt, []):
                if title not in have and title not in found:
                    found.append(title)
    return found


class RoleMatcher:
    """``search(title)`` like the old compiled regex: truthy when the title names a target role.

    No roles at all means an unfinished profile with no role constraint, so every
    non-empty title matches (another person's families are never injected).
    """

    def __init__(self, roles: list[str], related: list[str] | None = None, excluded: list[str] | None = None):
        self.roles = [str(r) for r in roles if str(r).strip()]
        self.related = [str(r) for r in (related or []) if str(r).strip()]
        self.excluded = [e for e in (" ".join(tokens(str(x))) for x in excluded or []) if e]
        self._alts = [alt for role in self.roles + self.related for alt in alternatives(role)]
        # A phrase that tokenises to nothing useful ("C++") still matches as written.
        self._phrases = [re.compile(r"(?i)(?<![a-z0-9])" + re.escape(r.strip()) + r"(?![a-z0-9])")
                         for r in self.roles + self.related]

    def __bool__(self) -> bool:
        return True

    def names(self) -> list[str]:
        return self.roles + self.related

    def _excluded(self, words: list[str]) -> bool:
        joined = " " + " ".join(words) + " "
        return any(" " + phrase + " " in joined for phrase in self.excluded)

    def search(self, title: str):
        title = str(title or "")
        if not title.strip():
            return None
        words = tokens(title)
        if self.excluded and self._excluded(words):
            return None
        if not self.roles and not self.related:
            return True
        if any(pattern.search(title) for pattern in self._phrases):
            return True
        have = _expanded(words)
        for head, quals in self._alts:
            heads = {head, *HEAD_EQUIVALENTS.get(head, ())}
            if heads & have and quals <= have:
                return True
        return None

    def which(self, title: str) -> str | None:
        """The target or related title this title matched, for the search report."""
        if not self.search(title):
            return None
        have = _expanded(tokens(title))
        for role in self.roles + self.related:
            if any(({h, *HEAD_EQUIVALENTS.get(h, ())} & have) and q <= have for h, q in alternatives(role)):
                return role
        return self.roles[0] if self.roles else None
