"""The sponsorship gate: excludes only on the posting's own words, ranks everything else."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

import pytest
from backend.services.sponsorship import (
    SponsorIndex, evaluate, is_cap_exempt, load_rules, normalize, resolve_tier, screen, split_sentences,
)

EXCLUDED = [
    "Must be authorized to work in the United States without sponsorship now or in the future.",
    "We are unable to sponsor work visas for this position.",
    "This employer will not sponsor H-1B applicants.",
    "Visa sponsorship is not available for this role.",
    "No visa sponsorship is provided.",
    "Must be a U.S. citizen.",
    "Applicants must be US citizens or permanent residents (green card holders) only.",
    "Active Secret security clearance required.",
    "US citizenship required due to ITAR regulations.",
    "We do not sponsor employment visas.",
    "This position is open to US persons only (EAR).",
    "Candidates requiring sponsorship will not be considered.",
    "Must not require sponsorship for employment visa status.",
    # "Current or future" wording (a live Greenhouse posting, 23 Sep 2026, slipped through before).
    "Authorization to work in the United States without current or future sponsorship.",
    "Must be authorized to work in the U.S. without the need for current or future visa sponsorship.",
    "Applicants must not require present or future employer sponsorship.",
    "The posting excludes current/future sponsorship.",
    # Citizenship wording with the verb first (Babel Street, Torc Robotics, 23 Sep 2026).
    "This position requires U.S. citizenship due to federal government contracting requirements.",
    "Accordingly, only U.S. citizens are eligible for this role.",
    "This role is open only to United States citizens.",
    # "Visa" between the verb and "sponsorship" (True Anomaly, 23 Sep 2026, slipped through before).
    "We are unable to provide visa sponsorship.",
    "We cannot offer H-1B sponsorship for this position.",
    "The company will not provide visa sponsorship for this role.",
    # "Not eligible for sponsorship" (end-to-end test, 24 Sep 2026, slipped through before).
    "This position is not eligible for visa sponsorship.",
    "This role is not eligible for sponsorship.",
    # A real refusal that also names a word about text is still a refusal.
    "No visa sponsorship is offered for this role, as stated in the text.",
    # A guard covers what it negates, not a refusal after "but".
    "No visa-sponsorship or citizenship sentence was visible, but the posting says: without sponsorship now or in the future.",
    "No security clearance is required, but US citizenship is required.",
]

KEPT = [
    "Must be authorized to work in the US.",
    "Legally authorized to work in the United States; employment eligibility verification (E-Verify) on hire.",
    "No security clearance required for this position.",
    "This role does not require sponsorship or a clearance.",
    "You will report to the executive sponsor for the program.",
    "Join our sponsored content team.",
    "We build data platforms for hospitals.",
    # Absence claims: restrictive-sounding words that only say the posting lacks them.
    "The posting contains an EEO/affirmative-action statement but no visa sponsorship, work-authorization, citizenship, clearance or ITAR/EAR language of any kind.",
    "No citizenship, clearance or ITAR language appears in this posting.",
    "The employer states no sponsorship or work-authorization language in the listing.",
    "No visa or citizenship restrictions stated in the posting.",
    "The posting says nothing about current or future sponsorship.",
    "The listing is silent on current or future visa sponsorship.",
    # A researcher's note on what the page did NOT show (Johns Hopkins, live Azure search, 24 Sep 2026;
    # the Ireland wording from the 24 Sep profile test). Built into the gate for every profile.
    "No visa-sponsorship, work-authorization, citizenship, clearance, ITAR/EAR, or US-person sentence was visible in the official posting text reviewed.",
    "The pages reviewed do not show the precise sentence about citizenship or security clearance.",
    "Citizenship or clearance language was not found in the posting.",
    "No sponsorship refusal, citizenship requirement or ITAR wording was found on the page.",
    # The application form's question asks; it does not refuse (SimpliSafe, 23 Sep 2026).
    "Will you now or in the future require sponsorship?",
    "Do you now, or will you in the future, require visa sponsorship to work in the US?",
    "The application asks about present or future sponsorship but does not state whether it is available.",
    "This role does not require U.S. citizenship.",
    "We are able to provide visa sponsorship for this role.",
    "",
]


@pytest.mark.parametrize("sentence", EXCLUDED)
def test_refusals_and_citizenship_requirements_are_excluded_with_the_sentence(sentence):
    result = screen(sentence)
    assert result.verdict == "EXCLUDED", sentence
    assert result.reason in {"no_sponsorship", "cannot_hire"}
    assert result.sentence and result.sentence in sentence.replace("U.S.", "US")


@pytest.mark.parametrize("sentence", KEPT)
def test_authorization_wording_negations_and_innocent_nouns_never_exclude(sentence):
    assert screen(sentence).verdict == "KEEP", sentence


def test_positive_wording_is_tier_a_and_silence_is_not_a_negative():
    positive = screen("Visa sponsorship is available for this role. We are open to candidates requiring sponsorship.")
    assert positive.verdict == "KEEP" and positive.reason == "explicit_sponsorship" and positive.evidence
    silent = screen("Build streaming pipelines with Kafka and Flink.")
    assert silent.verdict == "KEEP" and silent.reason == "silent"
    assert resolve_tier(positive, cap_exempt=False, approvals=0) == "A"
    assert resolve_tier(silent, cap_exempt=False, approvals=0) == "C"
    assert resolve_tier(silent, cap_exempt=False, approvals=12) == "B"
    assert resolve_tier(silent, cap_exempt=True, approvals=0) == "S"
    # An exclusion beats every ranking signal, even a cap-exempt employer with history.
    assert resolve_tier(screen("We do not sponsor employment visas."), cap_exempt=True, approvals=500) == "EXCLUDED"


def test_refusal_buried_in_a_long_posting_is_still_found():
    posting = ("Data Engineer I. " + "Build pipelines with Kafka, Flink and Airflow on AWS. " * 20
               + "\n- Benefits: health, dental.\n- Note: this role does not offer visa sponsorship.\n- Apply today.")
    result = screen(posting)
    assert result.verdict == "EXCLUDED" and "does not offer visa sponsorship" in result.sentence


def test_sentence_splitter_keeps_abbreviations_together():
    parts = split_sentences("Must be a U.S. citizen. Great team, e.g. data engineers.")
    assert parts[0].startswith("Must be a US citizen")


def test_cap_exempt_uses_domain_known_list_and_name_signals():
    rules = load_rules()
    assert is_cap_exempt("University of Arkansas", "jobs.uark.edu", rules)[0]
    assert is_cap_exempt("Mayo Clinic", "", rules)[0], "the known list must be read from cap_exempt_signals"
    assert is_cap_exempt("Broad Institute", "", rules)[0]
    assert is_cap_exempt("St. Jude Children's Research Hospital", "", rules)[0]
    assert is_cap_exempt("Acme Widgets LLC", "acme.com", rules) == (False, "")
    assert is_cap_exempt("Acme Health", "", rules, employer_type="hospital")[0]


def test_normalize_drops_corporate_suffixes():
    assert normalize("Databricks, Inc.") == "databricks"
    assert normalize("Confluent Inc") == normalize("Confluent")
    assert normalize("AT&T Services, Inc.") == "at and t"


def test_index_lookup_and_tiers_against_the_uscis_csv(tmp_path):
    csv_path = ROOT / "data/sponsors/sponsors-uscis.csv"
    if not csv_path.exists():
        pytest.skip("USCIS index not present")
    index = SponsorIndex(csv_path, tmp_path / "index.db")
    report = index.build()
    assert report["built"] and report["rows"] > 80000
    assert index.build()["reason"] == "already current"
    hit = index.lookup("Databricks")
    assert hit["found"] and hit["approvals"] > 0 and hit["years"]
    assert not index.lookup("Definitely Not A Real Employer 12345")["found"]
    silent = "We build data platforms."
    assert evaluate("Databricks", silent, sponsor_index=index).tier == "B"
    assert evaluate("Acme Widgets LLC", silent, sponsor_index=index).tier == "C"
    assert evaluate("Mayo Clinic", silent, sponsor_index=index).tier == "S"
    assert evaluate("University of Arkansas", silent, "https://jobs.uark.edu/1", sponsor_index=index).tier == "S"
    assert evaluate("Acme", "Visa sponsorship is available.", sponsor_index=index).tier == "A"
    verdict = evaluate("Acme", silent + " We do not sponsor employment visas.", sponsor_index=index)
    assert verdict.excluded and verdict.triggering_sentence.startswith("We do not sponsor")
    assert verdict.evidence_json()["tier"] == "EXCLUDED"


def test_discovery_quote_counts_as_posting_wording():
    verdict = evaluate("Acme", "Great team.", extra_sentences=["Must be authorized to work in the US without sponsorship now or in the future."])
    assert verdict.excluded


def test_verdict_serializes_for_the_cli():
    data = evaluate("Acme", "We do not sponsor employment visas.").as_dict()
    assert data["excluded"] is True and data["tier"] == "EXCLUDED" and data["label"]
    assert data["screen"]["sentence"].startswith("We do not sponsor")


def test_line_breaks_never_double_the_full_stop():
    parts = split_sentences("Build pipelines.\nMust be authorized to work in the US without sponsorship now or in the future.\n- Apply today")
    assert parts == ["Build pipelines.", "Must be authorized to work in the US without sponsorship now or in the future.", "Apply today"]


def test_absence_notes_never_exclude_on_any_country_pack():
    """The built-in guards hold whatever a profile's own sponsorship.yml lists."""
    note = ("No visa-sponsorship, work-authorization, citizenship, clearance, ITAR/EAR, or US-person "
            "sentence was visible in the official posting text reviewed.")
    for pack in ("us", "ie"):
        rules = load_rules(str(ROOT / f"backend/countries/{pack}/sponsorship.yml"))
        assert screen(note, rules).verdict == "KEEP", pack
        assert screen("This position is not eligible for visa sponsorship.", rules).verdict == "EXCLUDED", pack
