"""A scanned PDF remains usable on a fresh install without system OCR tools."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))

from backend.services.intake import extract  # noqa: E402


def test_scanned_pdf_uses_bundled_ocr(tmp_path, monkeypatch):
    image = Image.new("RGB", (1700, 450), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    draw.text((70, 70), "Example Person", fill="black", font=font)
    draw.text((70, 155), "Customer support specialist in Dublin", fill="black", font=font)
    draw.text((70, 240), "Experienced with Zendesk and email support", fill="black", font=font)
    path = tmp_path / "scanned.pdf"
    image.save(path, "PDF", resolution=150)

    monkeypatch.setattr(extract.shutil, "which", lambda _name: None)
    blocks = extract.read(path)
    text = " ".join(value for _, value in blocks).casefold()
    assert "customer support specialist in dublin" in text
    assert "zendesk and email support" in text
