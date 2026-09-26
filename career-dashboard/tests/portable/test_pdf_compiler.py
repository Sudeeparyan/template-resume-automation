"""Tectonic becomes available to a dashboard that was already open."""

import os
from pathlib import Path

import pytest

from backend import pdf_compiler
from backend.scripts import validate_resume


@pytest.mark.skipif(os.name != "nt", reason="Windows PATH updates need this fallback")
def test_detects_installation_after_process_started(tmp_path, monkeypatch):
    import winreg

    compiler = tmp_path / ".local" / "bin" / "tectonic.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"test executable")
    monkeypatch.setenv("PATH", str(tmp_path / "old-path"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(winreg, "OpenKey", lambda *args: (_ for _ in ()).throw(FileNotFoundError()))

    assert os.path.normcase(pdf_compiler.tectonic_executable()) == os.path.normcase(str(compiler))


def test_pdf_preview_uses_bundled_renderer_without_poppler(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    pdf = tmp_path / "example.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf.open("wb") as output:
        writer.write(output)
    monkeypatch.setattr(validate_resume.shutil, "which", lambda _name: None)

    inspected = validate_resume.inspect_pdf(pdf, tmp_path / "preview")

    assert inspected["page_count"] == 1
    assert (tmp_path / "preview/page-01.png").is_file()
    assert inspected["pages"][0]["width_points"] == 612
