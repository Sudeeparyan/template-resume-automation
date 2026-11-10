"""Keep hidden characters and tool metadata out of every resume.

Deterministic, local cleaning covers text, PDF and DOCX exports:

- Text: hidden characters (zero-width, direction controls,
  byte-order marks, tag characters, variation selectors, invisible fillers) are
  dropped, unusual spaces become a plain space, and a Cyrillic or Greek look-alike
  letter inside an otherwise Latin word becomes its Latin twin. ``career.tex_escape``
  runs it on every value written into resume.tex, Resume Studio on every save, the
  cover letter before it is written, and ``validate_resume.py`` refuses a source that
  still has one.
- The compiled PDF keeps only its Title and Author, which the validator checks
  against the candidate; the producer, creator, subject, keywords, dates and any XMP
  packet are removed.

The prose itself is never reworded here: a resume's wording must stay the registered
wording in evidence.yml.
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


_DOCX_DROP_PREFIXES = ("customXml/", "word/webextensions/")
_DOCX_COMMENT_PART = re.compile(r"^word/(?:comments(?:Extended|Ids|Extensible)?|people)\.xml$", re.I)


def _xml_tree(data: bytes):
    import io
    import xml.etree.ElementTree as ET

    namespaces = []
    for _event, value in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        if value not in namespaces:
            namespaces.append(value)
    for prefix, uri in namespaces:
        if not re.fullmatch(r"ns\d+", prefix):
            ET.register_namespace(prefix, uri)
    return ET.fromstring(data)


def _xml_bytes(root) -> bytes:
    import xml.etree.ElementTree as ET

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _docx_drop_part(name: str) -> bool:
    return name.startswith(_DOCX_DROP_PREFIXES) or name == "docProps/custom.xml" or bool(_DOCX_COMMENT_PART.match(name))


def _clean_word_xml(data: bytes) -> tuple[bytes, list[str]]:
    import xml.etree.ElementTree as ET

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = _xml_tree(data)
    removed = []
    for parent in list(root.iter()):
        for child in list(parent):
            local = child.tag.rsplit("}", 1)[-1]
            if child.tag == w + "r":
                properties = child.find(w + "rPr")
                if properties is not None and (properties.find(w + "vanish") is not None or properties.find(w + "webHidden") is not None):
                    parent.remove(child)
                    removed.append("hidden run")
                    continue
            if local in {"commentRangeStart", "commentRangeEnd", "commentReference"}:
                parent.remove(child)
                removed.append(local)
            elif child.tag in {w + "del", w + "moveFrom"}:
                parent.remove(child)
                removed.append(local)
            elif child.tag in {w + "ins", w + "moveTo"}:
                index = list(parent).index(child)
                for grandchild in list(child):
                    child.remove(grandchild)
                    parent.insert(index, grandchild)
                    index += 1
                parent.remove(child)
                removed.append(local)
    for node in root.iter(w + "t"):
        cleaned = clean_text(node.text or "")
        if cleaned != (node.text or ""):
            node.text = cleaned
            removed.append("hidden character")
    return _xml_bytes(root), removed


def _clean_docx_relationships(data: bytes, name: str, dropped: set[str]) -> bytes:
    import posixpath

    root = _xml_tree(data)
    relationship = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    base = "" if name == "_rels/.rels" else name.split("/_rels/", 1)[0]
    for node in list(root.findall(relationship)):
        target = str(node.get("Target") or "").replace("\\", "/").lstrip("/")
        target = posixpath.normpath(posixpath.join(base, target))
        if target in dropped or target.startswith(_DOCX_DROP_PREFIXES):
            root.remove(node)
    return _xml_bytes(root)


def _clean_content_types(data: bytes, dropped: set[str]) -> bytes:
    root = _xml_tree(data)
    override = "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
    for node in list(root.findall(override)):
        if str(node.get("PartName") or "").lstrip("/") in dropped:
            root.remove(node)
    return _xml_bytes(root)


def _clean_core(data: bytes, title: str, author: str) -> bytes:
    import xml.etree.ElementTree as ET

    root = _xml_tree(data)
    title_tag = "{http://purl.org/dc/elements/1.1/}title"
    author_tag = "{http://purl.org/dc/elements/1.1/}creator"
    for node in list(root):
        if node.tag not in {title_tag, author_tag}:
            root.remove(node)
    for tag, value in ((title_tag, title), (author_tag, author)):
        node = root.find(tag)
        if node is None:
            node = ET.SubElement(root, tag)
        node.text = value
    return _xml_bytes(root)


def clean_docx(data: bytes, *, title: str = "CV", author: str = "") -> tuple[bytes, list[str]]:
    """Clean an app-owned DOCX without changing its visible evidence-backed wording."""
    return _clean_docx_finish(data, title, author)


def _clean_docx_finish(data: bytes, title: str, author: str) -> tuple[bytes, list[str]]:
    import io
    import zipfile

    actions = []
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        dropped = {info.filename for info in source.infolist() if _docx_drop_part(info.filename)}
        actions.extend("drop " + name for name in sorted(dropped))
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename in dropped:
                    continue
                raw = source.read(info.filename)
                if info.filename == "docProps/core.xml":
                    raw = _clean_core(raw, title, author)
                elif info.filename == "docProps/app.xml":
                    root = _xml_tree(raw)
                    for node in list(root):
                        root.remove(node)
                    raw = _xml_bytes(root)
                elif info.filename == "[Content_Types].xml":
                    raw = _clean_content_types(raw, dropped)
                elif info.filename.endswith(".rels"):
                    raw = _clean_docx_relationships(raw, info.filename, dropped)
                elif info.filename.startswith("word/") and info.filename.endswith(".xml"):
                    raw, changed = _clean_word_xml(raw)
                    actions.extend(changed)
                target.writestr(info, raw)
    return output.getvalue(), list(dict.fromkeys(actions))


def docx_leftovers(data: bytes, *, title: str | None = None, author: str | None = None) -> list[str]:
    """Metadata, hidden content and revision residue that a clean DOCX must not retain."""
    import io
    import zipfile

    problems = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        problems += [name for name in names if _docx_drop_part(name)]
        core = _xml_tree(archive.read("docProps/core.xml"))
        allowed = {"{http://purl.org/dc/elements/1.1/}title", "{http://purl.org/dc/elements/1.1/}creator"}
        problems += ["core property " + node.tag.rsplit("}", 1)[-1] for node in core if node.tag not in allowed]
        values = {node.tag.rsplit("}", 1)[-1]: node.text or "" for node in core}
        if title is not None and values.get("title") != title:
            problems.append("title does not match the export")
        if author is not None and values.get("creator") != author:
            problems.append("author does not match the export")
        if "docProps/app.xml" in names:
            app = _xml_tree(archive.read("docProps/app.xml"))
            problems += ["application property " + node.tag.rsplit("}", 1)[-1] for node in app if (node.text or "").strip()]
        for name in names:
            if name.startswith("word/") and name.endswith(".xml"):
                root = _xml_tree(archive.read(name))
                for node in root.iter():
                    local = node.tag.rsplit("}", 1)[-1]
                    if local in {"vanish", "webHidden", "ins", "del", "moveFrom", "moveTo", "commentReference"}:
                        problems.append(name + ": " + local)
                    if local == "t":
                        problems += [name + ": " + mark for mark in find_marks(node.text or "")]
    return list(dict.fromkeys(problems))
