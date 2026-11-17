"""Dated public Irish permit facts and their freshness boundary."""
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml

RULES = Path(__file__).resolve().parents[1] / "countries/ie/permit-rules.yml"
WARN_DAYS = 30


def today():
    return datetime.now(ZoneInfo("Europe/Dublin")).date()


def load_rules(path=None):
    rules = yaml.safe_load(Path(path or RULES).read_text(encoding="utf-8"))
    if not isinstance(rules, dict):
        raise ValueError("Irish permit rules must be a YAML mapping.")
    return rules


def freshness(*, on=None, rules=None):
    on = on or today()
    rules = load_rules() if rules is None else rules
    effective, verified, review = (date.fromisoformat(str(rules[k])) for k in ("effective_from", "verified_at", "review_after"))
    if not effective <= verified <= review or not rules.get("version"):
        raise ValueError("Irish permit rules have inconsistent dates or no version.")
    days = (review - on).days
    state = "stale" if not effective <= on <= review else "due_soon" if days <= WARN_DAYS else "current"
    message = ""
    if state == "stale":
        message = (f"Irish employment-permit thresholds were checked on {verified} and due for review on {review}. "
                   "Salary-threshold checks show 'unknown' until official facts are re-verified.")
    elif state == "due_soon":
        message = f"Irish employment-permit thresholds (checked {verified}) are due for review on {review}. Re-verify official facts."
    if on < effective:
        message = f"Irish employment-permit thresholds take effect on {effective}. Checks show 'unknown' before that date."
    return {"state": state, "version": rules["version"], "verified_at": str(verified), "effective_from": str(effective),
            "review_after": str(review), "days_left": days, "message": message}
