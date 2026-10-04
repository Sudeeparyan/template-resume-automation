"""Official-format XLSX fixtures exercise source totals and publication privacy."""
from io import BytesIO
import csv
import hashlib
from pathlib import Path
from zipfile import ZipFile
from xml.sax.saxutils import escape

import pytest
import yaml

from backend.scripts.import_dete_permits import import_years, parse_workbook, workbook_url
from backend.permits.employer_names import is_individual
from backend.permits.history import FIELDS, decode_monthly
from backend.paths import COUNTRIES


def xlsx(rows):
    xml_rows = []
    for number, values in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(values):
            reference = chr(ord("A") + column) + str(number)
            if isinstance(value, int):
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        xml_rows.append(f'<row r="{number}">' + "".join(cells) + '</row>')
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Export" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + '</sheetData></worksheet>')
    return stream.getvalue()


@pytest.mark.parametrize("layout", ["pivot", "2024", "2025", "2026"])
def test_each_official_header_layout_reconciles_all_rows_before_filtering(layout):
    if layout == "pivot":
        rows = [["", "Grand Total", "2022", ""], ["", "", "Issued", ""], ["Employer Name", "", "Jan", "Feb"],
                ["Grand Total", 8, 3, 5], ["Example Ireland Limited", 5, 2, 3], ["Example Person", 3, 1, 2]]
    elif layout == "2024":
        rows = [["Employer Name", "Grand Total", "Jan", "Feb"], ["Grand Total", 8, 3, 5],
                ["Example Ireland Limited", 5, 2, 3], ["Example Person", 3, 1, 2]]
    else:
        labels = ["", "January", "February", "Grand Total"] if layout == "2025" else ["Employer Name", "Permits Issued Jan", "Permits Issued Feb", "Permits Issued Grand Total"]
        rows = [labels, ["Example Ireland Limited", 2, 3, 5], ["Example Person", 1, 2, 3], ["Total", 3, 5, 8]]
    records, meta = parse_workbook(xlsx(rows), 2026)
    assert len(records) == 1 and records[0]["employer_key"] == "example"
    assert records[0]["monthly_permits"] == {"2026-01": 2, "2026-02": 3}
    assert meta["source_total"] == 8 and meta["published_permits"] == 5
    assert meta["dropped_individual_or_ambiguous_rows"] == 1 and meta["dropped_permits"] == 3


@pytest.mark.parametrize("name", ["Example Person", "Dr. Example Person", "Example Person & Other Person",
                                   "Example Person t/a Example Services", "Example Person Farm", "Example Person Household"])
def test_individuals_sole_traders_and_ambiguous_business_names_are_private(name):
    assert is_individual(name)


@pytest.mark.parametrize("name", ["Example Person Limited", "Example Designated Activity Company", "Example University", "Health Service Executive"])
def test_corporate_and_institutional_employers_are_publishable(name):
    assert not is_individual(name)


def test_source_totals_fail_closed_and_do_not_write_partial_output(tmp_path):
    malformed = xlsx([["Employer Name", "Jan", "Grand Total"], ["Example Ltd", 3, 3], ["Total", 2, 2]])
    with pytest.raises(ValueError, match="totals do not reconcile"):
        parse_workbook(malformed, 2026)
    output = tmp_path / "sponsors-dete.csv"
    output.write_text("previous validated snapshot", encoding="utf-8")
    def downloader(url):
        return b'<a href="/en/publications/publication-files/permits-issued-to-companies-2026.xlsx">companies</a>' if url.endswith(".html") else malformed
    with pytest.raises(ValueError):
        import_years([2026], output, downloader=downloader)
    assert output.read_text() == "previous validated snapshot"


def test_changed_noncontiguous_month_header_is_refused():
    with pytest.raises(ValueError, match="contiguous"):
        parse_workbook(xlsx([["Employer Name", "Jan", "Mar", "Grand Total"], ["Total", 1, 2, 3]]), 2026)


def test_only_one_official_workbook_link_is_accepted():
    link = "https://enterprise.gov.ie/en/publications/publication-files/permits-issued-to-companies-2026.xlsx"
    assert workbook_url(f'<a href="{link}">companies</a>', 2026) == link
    with pytest.raises(ValueError):
        workbook_url('<a href="https://example.org/permits-issued-to-companies-2026.xlsx">companies</a>', 2026)


def test_import_metadata_has_hashes_totals_and_no_dropped_names(tmp_path):
    data = xlsx([["Employer Name", "Jan", "Grand Total"], ["Example Ltd", 2, 2], ["Example Person", 1, 1], ["Total", 3, 3]])
    def downloader(url):
        return b'<a href="/en/publications/publication-files/permits-issued-to-companies-2026.xlsx">companies</a>' if url.endswith(".html") else data
    output = tmp_path / "sponsors-dete.csv"
    result = import_years([2026], output, downloader=downloader)
    assert output.read_text(encoding="utf-8").splitlines() == [",".join(FIELDS), "Example Ltd,2026,2,01:2"]
    assert result["sources"][0]["months_covered"] == ["2026-01"]
    assert result["csv_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["sources"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert "Example Person" not in output.read_text() + output.with_suffix(".meta.yml").read_text()
    assert not list(tmp_path.glob("*.xlsx"))


def test_bundled_source_totals_hash_and_privacy_filter_reconcile():
    path = COUNTRIES / "ie/sponsors-dete.csv"
    assert path.stat().st_size < 3_000_000  # the compact format; the release scan enforces the same limit
    metadata = yaml.safe_load(path.with_suffix(".meta.yml").read_text(encoding="utf-8"))
    assert metadata["csv_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    expected = {2022: 39955, 2023: 30981, 2024: 39390, 2025: 31044, 2026: 29989}
    published = {source["year"]: set(source["months_covered"]) for source in metadata["sources"]}
    by_year = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            assert not is_individual(record["employer"])
            year = int(record["year"])
            monthly = decode_monthly(record["monthly_permits"], year)
            assert sum(monthly.values()) == int(record["permits"]) and set(monthly) <= published[year]
            by_year[year] = by_year.get(year, 0) + int(record["permits"])
    for source in metadata["sources"]:
        assert source["source_total"] == expected[source["year"]]
        assert by_year[source["year"]] + source["dropped_permits"] == source["source_total"]
        assert source["published_raw_rows"] + source["dropped_individual_or_ambiguous_rows"] == source["source_rows"]
    assert metadata["sources"][-1]["months_covered"][-1] == "2026-09"
