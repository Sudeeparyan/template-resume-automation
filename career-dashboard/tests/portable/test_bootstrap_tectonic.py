"""The launcher's pinned Tectonic: the right asset, a checked digest, only the binary extracted."""

import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("bootstrap_under_test", ROOT / "scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)


def test_each_computer_gets_its_pinned_build():
    assert bootstrap.tectonic_asset("Windows", "AMD64")[0].endswith("x86_64-pc-windows-msvc.zip")
    assert bootstrap.tectonic_asset("Windows", "ARM64")[0].endswith("x86_64-pc-windows-msvc.zip")
    assert bootstrap.tectonic_asset("Darwin", "arm64")[0].endswith("aarch64-apple-darwin.tar.gz")
    assert bootstrap.tectonic_asset("Linux", "x86_64")[0].endswith("x86_64-unknown-linux-musl.tar.gz")
    assert bootstrap.tectonic_asset("SunOS", "sparc") is None
    assert all(len(digest) == 64 for _, digest in bootstrap.TECTONIC_ASSETS.values())


@pytest.fixture
def archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        zipped.writestr("tectonic.exe", b"MZ compiler")
        zipped.writestr("../outside.txt", b"never extracted")
    return stream.getvalue()


def test_the_download_is_checked_then_only_the_binary_is_taken(tmp_path, monkeypatch, archive):
    from backend import pdf_compiler

    monkeypatch.setattr(pdf_compiler, "tectonic_executable", lambda: None)
    monkeypatch.setattr(bootstrap, "TOOLS", tmp_path / ".tools")
    monkeypatch.setattr(bootstrap, "tectonic_asset", lambda: ("tectonic-x.zip", hashlib.sha256(archive).hexdigest()))
    installed = bootstrap.install_tectonic(download=lambda url: archive)
    assert installed == tmp_path / ".tools" / f"tectonic-{bootstrap.TECTONIC_VERSION}" / "tectonic.exe"
    assert installed.read_bytes() == b"MZ compiler"
    assert not (tmp_path / "outside.txt").exists() and not list(tmp_path.rglob("outside.txt"))


def test_a_tampered_download_installs_nothing(tmp_path, monkeypatch, archive):
    from backend import pdf_compiler

    monkeypatch.setattr(pdf_compiler, "tectonic_executable", lambda: None)
    monkeypatch.setattr(bootstrap, "TOOLS", tmp_path / ".tools")
    monkeypatch.setattr(bootstrap, "tectonic_asset", lambda: ("tectonic-x.zip", "0" * 64))
    with pytest.raises(ValueError, match="SHA-256"):
        bootstrap.install_tectonic(download=lambda url: archive)
    assert not (tmp_path / ".tools").exists()


def test_an_installed_compiler_is_left_alone(monkeypatch):
    from backend import pdf_compiler

    monkeypatch.setattr(pdf_compiler, "tectonic_executable", lambda: "C:/tools/tectonic.exe")
    assert bootstrap.install_tectonic(download=lambda url: pytest.fail("nothing should be downloaded")) is None
