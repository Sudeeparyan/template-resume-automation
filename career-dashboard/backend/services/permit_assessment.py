"""Compatibility facade for the deterministic Irish permit assessment."""
from backend.permits import assessment, rules

RULES = rules.RULES
WARN_DAYS = rules.WARN_DAYS


def load_rules():
    return rules.load_rules(RULES)


def freshness(*, on=None, rules=None):
    from backend.permits.rules import freshness as check
    return check(on=on, rules=load_rules() if rules is None else rules)


def assess(job, salary, profile, sponsorship, *, on=None, **kwargs):
    kwargs.setdefault("rules", load_rules())
    return assessment.assess(job, salary, profile, sponsorship, on=on, **kwargs)


def personal_floor(profile, *, on=None):
    return assessment.personal_floor(profile, on=on, rules=load_rules())
