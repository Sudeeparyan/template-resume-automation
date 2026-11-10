"""Practice-mode switches, synthetic markers and local fictional postings."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

MARKER = ".career-synthetic-demo.json"
REGISTRY_MARKER = "demo-registry.json"
PRACTICE_PORT = 8010

PRACTICE_POSTINGS = [
    {
        "company": "Example Software Studio",
        "title": "Graduate Software Engineer",
        "location": "Dublin, Ireland",
        "url": f"http://127.0.0.1:{PRACTICE_PORT}/demo/postings/software",
        "description": (
            "Example Software Studio is a fictional employer offering a synthetic practice posting, not a real vacancy.\n\n"
            "Responsibilities: You will build small Python services, write SQL queries, and use Git for version control.\n"
            "Required: Experience with Python and SQL through academic or personal projects.\n"
            "Required: Knowledge of Git and software testing.\n"
            "Preferred: Experience with REST API development.\n"
            "This practice posting does not mention permits or sponsorship. There is no external application."
        ),
    },
    {
        "company": "Example Analytics Lab",
        "title": "Junior Data Analyst",
        "location": "Cork, Ireland",
        "url": f"http://127.0.0.1:{PRACTICE_PORT}/demo/postings/analytics",
        "description": (
            "Example Analytics Lab is a fictional employer offering a synthetic practice posting, not a real vacancy.\n\n"
            "Responsibilities: You will use SQL and Python to validate datasets and summarise findings.\n"
            "Required: Knowledge of SQL, Python, data quality and dashboards through projects.\n"
            "Preferred: Experience with Power BI or another dashboard tool.\n"
            "This practice posting does not mention permits or sponsorship. There is no external application."
        ),
    },
    {
        "company": "Example Systems Club",
        "title": "Graduate Software Engineer, Practice Search",
        "location": "Dublin, Ireland",
        "url": f"http://127.0.0.1:{PRACTICE_PORT}/demo/postings/search-result",
        "description": (
            "Example Systems Club is a fictional employer returned by the local practice provider, not a live vacancy.\n\n"
            "Responsibilities: You will build Python services and use Git to review project changes.\n"
            "Required: Academic project experience with Python, SQL, Git and REST API development.\n"
            "Preferred: Knowledge of software testing.\n"
            "No permit statement is included. There is no external application."
        ),
    },
]


def demo_mode(services) -> bool:
    """True when this profile has demo mode on (the ``demo_mode`` preference)."""
    return str(services.pref("demo_mode", "0")).lower() in ("1", "true", "yes", "on")


def screening_mode(services) -> str:
    value = str(services.pref("screening_mode", "careful") or "careful").casefold()
    return value if value in {"careful", "review"} else "careful"


def flexible_screening(services) -> bool:
    """Keep borderline postings for review, without relaxing resume evidence or readiness."""
    return demo_mode(services) or screening_mode(services) == "review"


def synthetic_demo(root: Path) -> bool:
    """True only when both the workspace and candidate configuration carry synthetic markers."""
    root = Path(root)
    try:
        marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
        profile = yaml.safe_load((root / "data/config/profile.yml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return False
    return marker.get("synthetic") is True and profile.get("synthetic_demo") is True


def practice_posting(slug: str) -> dict:
    for posting in PRACTICE_POSTINGS:
        if posting["url"].rsplit("/", 1)[-1] == slug:
            return posting
    raise ValueError("No such practice posting")
