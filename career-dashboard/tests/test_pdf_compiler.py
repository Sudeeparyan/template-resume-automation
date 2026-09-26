"""Tectonic becomes available to a dashboard that was already open."""

import os
from pathlib import Path

import pytest

from backend import pdf_compiler


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
