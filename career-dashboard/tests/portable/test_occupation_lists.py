"""Official occupation-table import and conservative deterministic classification."""

from datetime import date, timedelta
import hashlib
from html import escape
import importlib.util
import io
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from backend.permits import occupations

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("import_occupation_lists", REPO / "scripts/import_occupation_lists.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def docx(rows, merges=None):
    merges = merges or {}
    row_xml = []
    for number, row in enumerate(rows):
        cells = []
        for column, text in enumerate(row):
            prop = '<w:vMerge w:val="' + merges[(number, column)] + '"/>' if (number, column) in merges else ""
            paragraphs = "".join("<w:p><w:r><w:t>" + escape(part) + "</w:t></w:r></w:p>" for part in text.split("\n"))
            cells.append("<w:tc><w:tcPr>" + prop + "</w:tcPr>" + paragraphs + "</w:tc>")
        row_xml.append("<w:tr>" + "".join(cells) + "</w:tr>")
    xml = '<w:document xmlns:w="' + importer.NS["w"] + '"><w:body><w:tbl>' + "".join(row_xml) + "</w:tbl></w:body></w:document>"
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return stream.getvalue()


HEADER = ["SOC-3", "Employment category", "SOC-4", "Employments"]


def test_docx_import_preserves_vertical_merges_and_verbatim_exception_clauses():
    wording = "Other service managers\n(with the exception of Safety Manager)"
    data = docx([HEADER, ["125", "Managers in Other Services", "1251", "Property managers"],
                 ["", "", "1259", wording]], merges={(1, 0): "restart", (1, 1): "restart",
                                                      (2, 0): "continue", (2, 1): "continue"})
    rows = importer.parse_document(data, "ineligible", source_url="https://enterprise.gov.ie/example.docx")
    assert rows[1]["soc3"] == "125" and rows[1]["category"] == "Managers in Other Services"
    assert rows[1]["title"] == wording
    assert rows[1]["exceptions"] == ["with the exception of Safety Manager)"]
    assert rows[1]["conditional"] is True
    assert rows[1]["source_sha256"] == hashlib.sha256(data).hexdigest()


def test_a_publisher_blank_category_is_carried_only_inside_the_same_soc3():
    data = docx([HEADER, ["341", "Artistic occupations", "3411", "Animation artist"],
                 ["", "", "3417", "Riggers (within the Games Industry)"]])
    rows = importer.parse_document(data, "critical", source_url="https://enterprise.gov.ie/example.docx")
    assert rows[1]["soc3"] == "341" and rows[1]["category"] == "Artistic occupations"
    assert rows[1]["conditional"] and "within the Games Industry" in rows[1]["qualifiers"][0]


@pytest.mark.parametrize("rows,match", [
    ([["SOC 3", "Employment category", "SOC 4", "Employments"], ["213", "ICT", "2136", "Programmers"]], "header"),
    ([HEADER, ["212", "ICT", "2136", "Programmers"]], "inconsistent SOC-3"),
    ([HEADER, ["213", "ICT", "213", "Programmers"]], "Invalid SOC-4"),
    ([HEADER, ["213", "ICT", "2136", ""]], "Missing occupation"),
    ([HEADER, ["213", "ICT", "2136", "Programmers"], ["213", "ICT", "2136", "Software developers"]], "Duplicate"),
    ([HEADER, ["", "", "2136", "Programmers"]], "Invalid or inconsistent"),
])
def test_changed_tables_fail_before_they_can_replace_published_data(rows, match):
    with pytest.raises(ValueError, match=match):
        importer.parse_document(docx(rows), "critical", source_url="https://enterprise.gov.ie/example.docx")


def test_orphan_merge_and_non_docx_source_are_refused():
    with pytest.raises(ValueError, match="Orphaned"):
        importer.parse_document(docx([HEADER, ["", "", "2136", "Programmers"]],
                                     merges={(1, 0): "continue"}), "critical", source_url="https://enterprise.gov.ie/example.docx")
    with pytest.raises(ValueError, match="not a readable Word"):
        importer.parse_document(b"<html>blocked download</html>", "critical", source_url="https://enterprise.gov.ie/example.docx")


def test_source_links_are_discovered_only_on_the_official_https_host():
    html = b'<a href="/en/publications/publication-files/critical-skills-occupations-list.docx">Download</a>'
    url = importer.find_document(html, importer.SOURCES["critical"]["page_url"], "critical")
    assert url == "https://enterprise.gov.ie/en/publications/publication-files/critical-skills-occupations-list.docx"
    with pytest.raises(ValueError, match="official HTTPS"):
        importer.find_document(b'<a href="http://example.test/critical-occupations.docx">Wrong</a>',
                               importer.SOURCES["critical"]["page_url"], "critical")
    with pytest.raises(ValueError, match="found 0"):
        importer.find_document(b"No DOCX here", importer.SOURCES["critical"]["page_url"], "critical")


def test_offline_refresh_needs_an_explicit_check_date_and_replaces_only_validated_tables(tmp_path, capsys):
    sources = tmp_path / "originals"
    sources.mkdir()
    critical = docx([HEADER, ["213", "ICT", "2136", "Programmers"]])
    ineligible = docx([HEADER, ["125", "Service managers", "1259", "Other service managers (with the exception of Safety Manager)"]])
    (sources / "critical-skills-occupations-list.docx").write_bytes(critical)
    ineligible_file = sources / "ineligible-occupations-list.docx"
    ineligible_file.write_bytes(ineligible)
    output = tmp_path / "occupations.yml"
    output.write_text("Previous checked dataset", encoding="utf-8")
    with pytest.raises(SystemExit):
        importer.main(["--source-dir", str(sources), "--output", str(output)])
    assert output.read_text(encoding="utf-8") == "Previous checked dataset"
    assert importer.main(["--source-dir", str(sources), "--output", str(output), "--verified-at", "2026-10-03"]) == 0
    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert data["sources"]["critical"]["sha256"] == hashlib.sha256(critical).hexdigest()
    assert len(data["entries"]) == 2 and data["verified_at"] == "2026-10-03"
    before = output.read_bytes()
    ineligible_file.write_bytes(b"Broken document")
    with pytest.raises(ValueError, match="not a readable Word"):
        importer.main(["--source-dir", str(sources), "--output", str(output), "--verified-at", "2026-10-03"])
    assert output.read_bytes() == before
    assert list(tmp_path.glob("occupations-*.yml")) == []
    capsys.readouterr()


def test_bundled_snapshot_has_complete_sources_codes_and_known_spot_checks():
    data = occupations.load_lists()
    by_id = {entry["id"]: entry for entry in data["entries"]}
    assert len(by_id) == len(data["entries"])
    assert data["sources"]["critical"]["row_count"] == 60
    assert data["sources"]["ineligible"]["row_count"] == 188
    assert data["sources"]["ineligible"]["soc4_count"] == 187
    assert "Programmers and software development professionals" == by_id["critical:2136"]["title"]
    assert "Safety Manager" in by_id["ineligible:1259"]["exceptions"][0]
    assert by_id["ineligible:all"]["title"] == "Domestic worker"
    for entry in data["entries"]:
        assert entry["source_url"].startswith("https://enterprise.gov.ie/")
        assert entry["source_sha256"] == data["sources"][entry["list"]]["sha256"]
        assert entry["category"] and entry["title"]
        if entry["soc4"] != "all":
            assert entry["soc3"] == entry["soc4"][:3]
    for row in occupations.load_keywords():
        assert f"{row['list']}:{row['soc4']}" in by_id
        assert row["title_aliases"] and row["duty_phrases"]


def classify(job):
    return occupations.classify(job, on=date.fromisoformat(occupations.load_lists()["verified_at"]))


def test_software_duties_match_with_exact_quotes_without_claiming_qualifications():
    description = "You will develop software for public data services. Work with the wider team."
    result = classify({"title": "Graduate Software Engineer", "description": description})
    assert result["classification"] == "critical" and result["soc4"] == "2136"
    match = result["matches"][0]
    assert match["duty_quote"] == "You will develop software for public data services."
    assert match["duty_quote"] in description
    assert match["quote"] == "Programmers and software development professionals"
    assert "Appears to match" in result["reason"]


@pytest.mark.parametrize("job", [
    {"title": "Software Engineer", "description": "Good communication required."},
    {"soc4": "2136"},
    {"title": "Business Analyst", "description": "Build big data analytics and data mining tools."},
    {"soc4": "1259", "soc4_verified": True, "title": "Safety Manager", "description": "Manage safety programmes."},
    {"soc4": "2419", "soc4_verified": True},
    {"title": "Data Analyst", "description": "Use SQL and Python."},
    {"title": "Software developer / Web developer", "description": "Software development and web development."},
    {"title": "Software Engineer", "description": "Team collaboration.", "duty_excerpts": ["Develop software."]},
    {"title": "Software Engineer", "description": "You will not develop software or write code."},
    {"soc4": "invalid"},
    {"soc4": "1221"},
    {"soc4": "2136", "soc4_verified": True, "employment_context": "private_home"},
])
def test_ambiguous_titles_codes_specialisms_and_exceptions_stay_unknown(job):
    assert classify(job)["classification"] == "unknown"


def test_verified_broad_code_and_unconditional_ineligible_role_are_dated_list_facts():
    assert classify({"soc4": "2136", "soc4_verified": True})["classification"] == "critical"
    receptionist = classify({"title": "Receptionist", "description": "Greet visitors at the reception desk."})
    assert receptionist["classification"] == "ineligible" and receptionist["soc4"] == "4216"
    neither = classify({"soc4": "1221", "soc4_verified": True})
    assert neither["classification"] == "neither" and not neither["matches"]


def test_stale_missing_or_malformed_data_does_not_create_a_list_match(tmp_path, monkeypatch):
    data = occupations.load_lists()
    stale = occupations.classify({"soc4": "2136", "soc4_verified": True},
                                 on=date.fromisoformat(data["review_after"]) + timedelta(days=1))
    assert stale["classification"] == "unknown" and "need review" in stale["reason"]
    path = tmp_path / "occupations.yml"
    monkeypatch.setattr(occupations, "LISTS_FILE", path)
    assert occupations.classify({"soc4": "2136", "soc4_verified": True})["classification"] == "unknown"
    path.write_text("not a mapping", encoding="utf-8")
    assert occupations.classify({"soc4": "2136", "soc4_verified": True})["classification"] == "unknown"


@pytest.mark.parametrize("phrase", ["you are eligible", "you qualify", "guarantee"])
def test_classifier_wording_never_reads_as_a_personal_permit_decision(phrase):
    for job in ({"soc4": "2136", "soc4_verified": True}, {"soc4": "1259", "soc4_verified": True},
                {"soc4": "1221", "soc4_verified": True}):
        result = classify(job)
        assert phrase not in result["reason"].casefold()
