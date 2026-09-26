"""Keep AI provenance marks out of every resume: the remove-ai-marks skill, built in.

Two deterministic passes, local and without the skill's HTTP service:

- Text (the skill's Layer A): hidden characters (zero-width, direction controls,
  byte-order marks, tag characters, variation selectors, invisible fillers) are
  dropped, unusual spaces become a plain space, and a Cyrillic or Greek look-alike
  letter inside an otherwise Latin word becomes its Latin twin. ``career.tex_escape``
  runs it on every value written into resume.tex, Resume Studio on every save, the
  cover letter before it is written, and ``validate_resume.py`` refuses a source that
  still has one.
- The compiled PDF (the skill's container strip) keeps only its Title and Author,
  which the validator checks against the candidate; the producer, creator, subject,
  keywords, dates and any XMP packet are removed.

The skill's Layer B (rewording the prose) is never applied to a resume: its wording
must stay the registered wording in evidence.yml.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

# Hidden characters outside Unicode's "format" category (Cf, handled by category).
_INVISIBLE = {
    0x034F,  # combining grapheme joiner
    0x115F, 0x1160, 0x3164, 0xFFA0,  # Hangul fillers
    0x180B, 0x180C, 0x180D, 0x180F,  # Mongolian variation selectors
}
_SPACES = set("             "
              "    　")
# Cyrillic and Greek letters drawn like Latin ones; replaced only inside a Latin word.
_LOOKALIKES = dict(zip(
    "АВЕКМНОРСТХаеорсухіјѕІЈЅΑΒΕΖΗΙΚΜΝΟΡΤΥΧο",
    "ABEKMHOPCTXaeopcyxijsIJSABEZHIKMNOPTYXo",
))
_WORD = re.compile(r"\w+")
_LATIN = re.compile(r"[A-Za-z]")
# The validator checks these two against the candidate; everything else is removed.
KEEP_PDF_FIELDS = ("/Title", "/Author")


def _hidden(c: str) -> bool:
    code = ord(c)
    return (unicodedata.category(c) == "Cf" or code in _INVISIBLE
            or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF)


def _mixed(word: str) -> bool:
    return bool(_LATIN.search(word)) and any(c in _LOOKALIKES for c in word)


def clean_text(text: str) -> str:
    """Drop hidden characters, plain every space, and fix look-alike letters in Latin words."""
    text = "".join(" " if c in _SPACES else c for c in text if not _hidden(c))
    return _WORD.sub(lambda m: "".join(_LOOKALIKES.get(c, c) for c in m[0]) if _mixed(m[0]) else m[0], text)


def find_marks(text: str) -> list[str]:
    """One line per kind of mark still in ``text``: what it is, how many, the first line."""
    counts: Counter[str] = Counter()
    first: dict[str, int] = {}
    for number, line in enumerate(text.splitlines() or [text], 1):
        labels = [f"U+{ord(c):04X} {unicodedata.name(c, 'unnamed')}" for c in line if _hidden(c) or c in _SPACES]
        labels += [f"look-alike letters in '{word}'" for word in _WORD.findall(line) if _mixed(word)]
        for label in labels:
            counts[label] += 1
            first.setdefault(label, number)
    return [f"{label} x{count} (first on line {first[label]})" for label, count in counts.items()]


def pdf_leftovers(path: Path) -> list[str]:
    """Metadata a cleaned resume PDF must no longer carry (empty when clean)."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    extra = sorted(key for key in (reader.metadata or {}) if key not in KEEP_PDF_FIELDS)
    if "/Metadata" in reader.trailer["/Root"]:
        extra.append("XMP packet")
    return extra


def clean_pdf(path: Path) -> list[str]:
    """Rewrite the PDF in place with only its Title and Author; return what was removed."""
    from pypdf import PdfReader, PdfWriter

    path = Path(path)
    removed = pdf_leftovers(path)
    if not removed:
        return []
    reader = PdfReader(str(path))
    writer = PdfWriter(clone_from=reader)
    writer.metadata = {key: value for key, value in (reader.metadata or {}).items() if key in KEEP_PDF_FIELDS}
    for node in (writer.root_object, *writer.pages):
        for key in ("/Metadata", "/PieceInfo"):
            if key in node:
                del node[key]
    temporary = path.with_name(path.name + ".clean")
    with open(temporary, "wb") as handle:
        writer.write(handle)
    os.replace(temporary, path)
    return removed
