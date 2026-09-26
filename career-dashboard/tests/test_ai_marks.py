"""AI marks (hidden characters, look-alike letters, PDF provenance metadata) never reach a resume."""
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai_marks import clean_pdf, clean_text, find_marks, pdf_leftovers
from career import tex_escape

NAME = "Chetan Babu M"


def test_hidden_characters_are_dropped_and_spaces_made_plain():
    marked = ("Built​ data‍ pipe­lines⁠ with Kafka and Flink﻿"
              " ‮SQL‬\U000E0041\U000E007F on AWS️")
    assert clean_text(marked) == "Built data pipelines with Kafka and Flink SQL on AWS"
    assert find_marks(clean_text(marked)) == []


def test_look_alike_letters_are_fixed_only_inside_latin_words():
    # Cyrillic а and о inside "Pandas" and "Python"; a whole Cyrillic word and real accents stay.
    assert clean_text("Pаndas and Pythоn") == "Pandas and Python"
    assert clean_text("Олена at Dräger, Montréal") == "Олена at Dräger, Montréal"


def test_find_marks_names_each_mark_and_its_first_line():
    found = find_marks("clean line\nsecond​ line​\nPаndas")
    assert found == ["U+200B ZERO WIDTH SPACE x2 (first on line 2)",
                     "look-alike letters in 'Pаndas' x1 (first on line 3)"]


def test_every_value_written_into_resume_tex_is_cleaned():
    assert tex_escape("SQL​ & Pythоn — 94%") == r"SQL \& Python --- 94\%"


def marked_pdf(path):
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.add_metadata({"/Title": NAME + " - Resume", "/Author": NAME, "/Producer": "xdvipdfmx (0.1)",
                         "/Creator": "LaTeX with hyperref", "/Subject": "Evidence-grounded resume",
                         "/Keywords": "Python, SQL", "/CreationDate": "D:20260925000000"})
    xmp = DecodedStreamObject()
    xmp.set_data(b"<x:xmpmeta><c2pa:manifest/><xmp:CreatorTool>AI</xmp:CreatorTool></x:xmpmeta>")
    writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def test_pdf_keeps_only_title_and_author(tmp_path):
    pdf = marked_pdf(tmp_path / "resume.pdf")
    assert pdf_leftovers(pdf) == ["/CreationDate", "/Creator", "/Keywords", "/Producer", "/Subject", "XMP packet"]
    assert clean_pdf(pdf) == ["/CreationDate", "/Creator", "/Keywords", "/Producer", "/Subject", "XMP packet"]
    reader = PdfReader(str(pdf))
    assert dict(reader.metadata) == {"/Title": NAME + " - Resume", "/Author": NAME}
    assert "/Metadata" not in reader.trailer["/Root"] and len(reader.pages) == 1
    assert pdf_leftovers(pdf) == [] and clean_pdf(pdf) == [], "a clean PDF is left untouched"
    assert not (tmp_path / "resume.pdf.clean").exists()
