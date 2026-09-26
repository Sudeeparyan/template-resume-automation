"""Turn uploaded documents into ordered, numbered blocks of text. No AI, nothing dropped.

Word files are read straight from their XML (standard library only): paragraphs in
order, headings (by style, or a short bold line), list items and tables as rows.
PDFs are read page by page with pypdf. Lines a converter wrapped mid-sentence are
joined back into one paragraph. Every block gets a stable id (P001, P002, ...)
so each profile fact can point back to the exact place it came from.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SUPPORTED = {".docx", ".pdf", ".txt", ".md"}
MAX_BYTES = 20 * 1024 * 1024
# A sentence can end with these; a line that does not was wrapped by a converter.
_ENDS = re.compile(r"[.!?:;”\"')\]]\s*$")


@dataclass
class Block:
    id: str
    kind: str          # heading | paragraph | list | table
    text: str
    source: str        # the uploaded file's name

    def as_dict(self) -> dict:
        return asdict(self)


def _is_heading_text(text: str) -> bool:
    words = text.split()
    return (0 < len(words) <= 12 and len(text) <= 90 and not _ENDS.search(text)
            and not text.endswith(",") and (text[:1].isupper() or text[:1].isdigit()))


def _docx_paragraph(p) -> tuple[str, str]:
    """(kind, text) for one w:p element."""
    parts = []
    for node in p.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            parts.append("\n")
    text = re.sub(r"[ \t ]+", " ", "".join(parts)).strip()
    props = p.find(W + "pPr")
    style = ""
    listed = False
    if props is not None:
        style_node = props.find(W + "pStyle")
        style = (style_node.get(W + "val") if style_node is not None else "") or ""
        listed = props.find(W + "numPr") is not None
    runs = [r for r in p.findall(W + "r") if "".join(t.text or "" for t in r.iter(W + "t")).strip()]
    bold = bool(runs) and all(
        (r.find(W + "rPr") is not None and r.find(W + "rPr").find(W + "b") is not None) for r in runs
    )
    if style.lower().startswith(("heading", "title", "subtitle")) or (bold and _is_heading_text(text)):
        return "heading", text
    if listed or style.lower().startswith("list"):
        return "list", text
    return "paragraph", text


def _docx_table(tbl) -> str:
    rows = []
    for tr in tbl.iter(W + "tr"):
        cells = []
        for tc in tr.findall(W + "tc"):
            cell = " ".join(_docx_paragraph(p)[1] for p in tc.iter(W + "p")).strip()
            cells.append(cell.replace("|", "/"))
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _docx(path: Path) -> list[tuple[str, str]]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    body = ElementTree.fromstring(xml).find(W + "body")
    items = []
    for child in list(body) if body is not None else []:
        if child.tag == W + "p":
            kind, text = _docx_paragraph(child)
            if text:
                items.append((kind, text))
        elif child.tag == W + "tbl":
            table = _docx_table(child)
            if table:
                items.append(("table", table))
    return items


def _pdf(path: Path) -> list[tuple[str, str]]:
    from pypdf import PdfReader

    items = []
    for number, page in enumerate(PdfReader(str(path)).pages, 1):
        text_content = page.extract_text() or ""
        if len(text_content.strip()) < 20:
            text_content = _ocr_pdf_page(path, number)
        for line in text_content.splitlines():
            text = re.sub(r"\s+", " ", line).strip()
            if not text:
                continue
            if re.match(r"^[•●▪–\-*]\s+", text):
                items.append(("list", re.sub(r"^[•●▪–\-*]\s+", "", text)))
            elif _is_heading_text(text) and (text.isupper() or len(text.split()) <= 6):
                items.append(("heading", text))
            else:
                items.append(("paragraph", text))
    return items


def _ocr_pdf_page(path: Path, page: int) -> str:
    """OCR an image-only page, using installed CLI tools or bundled Python libraries."""
    renderer = shutil.which("pdftoppm")
    ocr = shutil.which("tesseract")
    if renderer and ocr:
        with tempfile.TemporaryDirectory(prefix="career-ocr-") as directory:
            prefix = str(Path(directory) / "page")
            render = subprocess.run([renderer, "-f", str(page), "-l", str(page), "-r", "200", "-singlefile",
                                     "-png", str(path), prefix], capture_output=True, text=True, timeout=90)
            image = Path(prefix + ".png")
            if render.returncode or not image.is_file():
                raise ValueError(f"Could not render scanned PDF page {page} for OCR: {render.stderr.strip()[:180]}")
            result = subprocess.run([ocr, str(image), "stdout", "-l", "eng", "--psm", "6"],
                                    capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise ValueError(f"Could not OCR scanned PDF page {page}: {result.stderr.strip()[:180]}")
            return result.stdout
    try:
        import numpy as np
        import pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR

        document = pdfium.PdfDocument(str(path))
        try:
            image = document[page - 1].render(scale=200 / 72).to_pil()
            result, _elapsed = RapidOCR()(np.asarray(image.convert("RGB")))
        finally:
            document.close()
        return "\n".join(str(line[1]) for line in (result or []) if len(line) > 1)
    except ImportError as error:
        raise ValueError("This PDF contains a scanned page. Install the OCR dependencies and retry the build.") from error


def _text(path: Path) -> list[tuple[str, str]]:
    items = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text:
            items.append(("break", ""))
            continue
        if text.startswith("#"):
            items.append(("heading", text.lstrip("#").strip()))
        elif re.match(r"^[-*•]\s+", text):
            items.append(("list", re.sub(r"^[-*•]\s+", "", text)))
        elif text.startswith("|"):
            items.append(("table", text))
        else:
            items.append(("paragraph", text))
    return items


def _join_wrapped(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Join a paragraph line a converter broke mid-sentence to the line after it."""
    out: list[tuple[str, str]] = []
    for kind, text in items:
        if kind == "break":
            out.append((kind, text))
            continue
        if (out and kind == "paragraph" and out[-1][0] == "paragraph" and not _ENDS.search(out[-1][1])
                and text[:1] and (text[:1].islower() or text[:1] in ",;)-–—")):
            out[-1] = ("paragraph", out[-1][1] + " " + text)
        elif out and kind == "table" and out[-1][0] == "table":
            out[-1] = ("table", out[-1][1] + "\n" + text)
        else:
            out.append((kind, text))
    return [item for item in out if item[0] != "break"]


def read(path: Path, name: str | None = None) -> list[tuple[str, str]]:
    """(kind, text) items of one document, in reading order."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"{path.name}: upload a Word (.docx), PDF, text or Markdown file.")
    if suffix == ".docx":
        try:
            items = _docx(path)
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
            raise ValueError(f"{name or path.name} is not a readable Word document. Save it again as .docx and upload that.") from None
    elif suffix == ".pdf":
        try:
            items = _pdf(path)
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - a broken or encrypted PDF
            raise ValueError(f"{name or path.name} could not be read as a PDF ({type(error).__name__}).") from None
    else:
        items = _text(path)
    return _join_wrapped(items)


def blocks_for(files: list[tuple[Path, str]]) -> list[Block]:
    """Numbered blocks across every uploaded file, in upload order."""
    blocks: list[Block] = []
    for path, name in files:
        for kind, text in read(path, name):
            blocks.append(Block(id=f"P{len(blocks) + 1:03d}", kind=kind, text=text, source=name))
    return blocks


def source_markdown(blocks: list[Block], name: str, uploaded: str) -> str:
    """The readable copy of one document kept in data/context/sources/ (every block, with its id)."""
    lines = [f"# {name}", "",
             f"Uploaded {uploaded}. This is the document as it was read, block by block. The id in "
             "brackets is what each profile entry cites in its source_refs.", ""]
    for block in blocks:
        if block.source != name:
            continue
        if block.kind == "heading":
            lines += [f"## [{block.id}] {block.text}", ""]
        elif block.kind == "table":
            lines += [f"[{block.id}] table:", "", block.text, ""]
        elif block.kind == "list":
            lines += [f"- [{block.id}] {block.text}"]
        else:
            lines += [f"[{block.id}] {block.text}", ""]
    return "\n".join(lines).rstrip() + "\n"


def chunks(blocks: list[Block], limit: int = 12000, soft: int = 5000) -> list[list[Block]]:
    """Section-sized groups for the AI: split at headings once a group is big enough,
    never inside a block, never above `limit` characters unless one block is larger."""
    groups: list[list[Block]] = []
    current: list[Block] = []
    size = 0
    for block in blocks:
        length = len(block.text) + 8
        if current and ((block.kind == "heading" and size >= soft) or size + length > limit):
            groups.append(current)
            current, size = [], 0
        current.append(block)
        size += length
    if current:
        groups.append(current)
    return groups


def chunk_text(group: list[Block]) -> str:
    return "\n".join(f"[{b.id}] ({b.kind}) {b.text}" for b in group)
