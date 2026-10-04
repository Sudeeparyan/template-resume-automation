"""Public employer history is dated ranking evidence, never an eligibility claim."""
from datetime import date
import csv

import pytest

from backend.paths import COUNTRIES
from backend.permits.history import FIELDS, PermitHistoryIndex, decode_monthly, describe, encode_monthly, window_for
from backend.services import sponsorship


def index_at(tmp_path, records, aliases=None):
    path = tmp_path / "history.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for employer, year, monthly in records:
            writer.writerow({"employer": employer, "year": year, "permits": sum(monthly.values()),
                             "monthly_permits": encode_monthly(monthly)})
    return PermitHistoryIndex(path, tmp_path / "index.db", aliases=aliases or {})


def test_monthly_counts_are_stored_compactly_and_read_back_exactly():
    monthly = {"2026-01": 0, "2026-04": 1, "2026-09": 12}
    assert encode_monthly(monthly) == "04:1;09:12"
    assert decode_monthly("04:1;09:12", 2026) == {"2026-04": 1, "2026-09": 12}
    assert decode_monthly("", 2026) == {}
    for broken in ("13:1", "04:0", "04:1;04:2", "4:1", "04=1"):
        with pytest.raises(ValueError):
            decode_monthly(broken, 2026)


def test_counts_by_year_name_a_partial_year():
    record = {"by_year": {"2025": 12, "2026": 1234},
              "year_months": {"2025": [f"2025-{m:02d}" for m in range(1, 13)],
                              "2026": [f"2026-{m:02d}" for m in range(1, 10)]}}
    assert describe(record) == "2025: 12; 2026 (Jan–Sep): 1,234"


def test_bad_public_data_never_breaks_a_lookup(tmp_path):
    index = index_at(tmp_path, [("Example Limited", 2026, {"2026-01": 2})])
    text = index.csv_path.read_text(encoding="utf-8").replace("01:2", "01:3")  # counts no longer reconcile
    index.csv_path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="reconcile"):
        index.build()
    result = index.lookup("Example", on="2026-10-03")
    assert not result["found"] and result["match_type"] == "unavailable"


def test_rolling_history_uses_source_months_and_actual_24_month_boundary(tmp_path):
    index = index_at(tmp_path, [
        ("Example Ireland Limited", 2024, {"2024-09": 99, "2024-10": 2, "2024-11": 3, "2024-12": 4}),
        ("Example Ireland Limited", 2025, {f"2025-{month:02d}": 1 for month in range(1, 13)}),
        ("Example Ireland Limited", 2026, {"2026-01": 5, "2026-09": 6, "2026-10": 50})])
    result = index.lookup("Example", on=date(2026, 10, 3))
    assert result["found"] and result["match_type"] == "exact"
    assert result["by_year"] == {"2024": 108, "2025": 12, "2026": 61}
    assert result["permits_24_months"] == 32
    assert result["window_start"] == "2024-10-01" and result["window_end"] == "2026-09-30"
    assert "2024-09" not in result["months_covered"] and "2026-10" not in result["months_covered"]
    assert result["matched_names"] == ["Example Ireland Limited"] and result["source_urls"]
    assert window_for(date(2026, 1, 1)) == (date(2024, 1, 1), date(2025, 12, 31))


def test_aliases_trading_names_prefixes_and_boundaries_keep_match_types(tmp_path):
    index = index_at(tmp_path, [("Example Payments Limited", 2026, {"2026-01": 2}),
                                ("Other Ltd t/a Tradebrand Ireland Limited", 2026, {"2026-01": 3}),
                                ("Applegreen Services Limited", 2026, {"2026-01": 7})],
                     aliases={"short": ["example payments"]})
    assert index.lookup("Example", on="2026-10-03")["match_type"] == "prefix"
    assert index.lookup("Short", on="2026-10-03")["match_type"] == "alias"
    assert index.lookup("Tradebrand", on="2026-10-03")["match_type"] == "exact"
    assert not index.lookup("Apple", on="2026-10-03")["found"]


def test_ambiguous_group_is_not_attributed_and_remains_a_neutral_absence(tmp_path):
    records = [(f"Example {word} Limited", 2026, {"2026-01": 1}) for word in ("Payments", "Software", "Services", "Technology", "Research", "Data")]
    result = index_at(tmp_path, records).lookup("Example", on="2026-10-03")
    assert not result["found"] and result["match_type"] == "ambiguous" and result["permits_24_months"] == 0


def test_csv_changes_rebuild_and_missing_csv_is_neutral(tmp_path):
    index = index_at(tmp_path, [("Example Limited", 2026, {"2026-01": 2})])
    assert index.lookup("Example", on="2026-10-03")["permits_24_months"] == 2
    assert index.is_current()
    index.csv_path.unlink()
    assert not index.lookup("Example", on="2026-10-03")["found"]


def test_index_refuses_a_private_or_tampered_csv_record(tmp_path):
    index = index_at(tmp_path, [("Example Person", 2026, {"2026-01": 1})])
    with pytest.raises(ValueError, match="individual"):
        index.build()
    assert index.lookup("Example Person", on="2026-10-03")["match_type"] == "unavailable"


def test_dete_tier_b_is_only_ranking_and_explicit_posting_words_still_decide(tmp_path):
    index = index_at(tmp_path, [("Example Limited", 2026, {"2026-01": 2})])
    rules = sponsorship.load_rules(str(COUNTRIES / "ie/sponsorship.yml"))
    verdict = sponsorship.evaluate("Example", "Build internal reporting tools.", rules=rules, sponsor_index=index)
    assert verdict.tier == "B" and not verdict.h1b_found and verdict.permit_record["found"]
    assert "Example Limited" in verdict.label() and "DETE" in verdict.label()
    assert verdict.evidence_json()["permit_record"] == verdict.permit_record
    stricter = sponsorship.evaluate("Example", "Build internal reporting tools.", rules={**rules, "tier_b_min_permits": 3}, sponsor_index=index)
    assert stricter.tier == "C" and not stricter.excluded
    unknown = sponsorship.evaluate("Missing", "Build internal reporting tools.", rules=rules, sponsor_index=index)
    assert unknown.tier == "C" and not unknown.excluded
    refuses = sponsorship.evaluate("Example", "No visa sponsorship is available.", rules=rules, sponsor_index=index)
    assert refuses.excluded and refuses.triggering_sentence == "No visa sponsorship is available."


def test_evaluate_does_not_implicitly_consult_us_history(monkeypatch):
    rules = sponsorship.load_rules(str(COUNTRIES / "ie/sponsorship.yml"))
    rules = {**rules, "sponsor_index": ""}
    monkeypatch.setattr(sponsorship, "index", lambda: pytest.fail("USCIS must be selected explicitly"))
    assert sponsorship.evaluate("Example", "Build reporting tools.", rules=rules).tier == "C"


def test_us_index_keeps_its_public_history_interface():
    assert issubclass(sponsorship.SponsorIndex, PermitHistoryIndex)
    assert sponsorship.SponsorIndex.kind == "uscis"
