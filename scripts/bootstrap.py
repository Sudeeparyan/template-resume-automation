"""Install local dependencies, report optional tools, and start the loopback app.

Called by the Windows and macOS launchers. No profile data or secrets are copied.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "career-dashboard"
BACKEND = APP / "backend"
FRONTEND = APP / "frontend"
VENV = BACKEND / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
TOOLS = BACKEND / ".tools"
# Tectonic compiles the resume PDFs. When none is installed, the launcher fetches this release and
# checks it against the SHA-256 digest GitHub publishes for each asset (tectonic@0.17.0, 2026-07-27).
TECTONIC_VERSION = "0.17.0"
TECTONIC_URL = "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{version}/{name}"
TECTONIC_ASSETS = {
    ("windows", "x86_64"): ("tectonic-0.17.0-x86_64-pc-windows-msvc.zip",
                            "f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f"),
    ("darwin", "aarch64"): ("tectonic-0.17.0-aarch64-apple-darwin.tar.gz",
                            "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667"),
    ("darwin", "x86_64"): ("tectonic-0.17.0-x86_64-apple-darwin.tar.gz",
                           "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80"),
    ("linux", "x86_64"): ("tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz",
                          "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7"),
    ("linux", "aarch64"): ("tectonic-0.17.0-aarch64-unknown-linux-musl.tar.gz",
                           "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8"),
}


def run(args: list[str], *, cwd: Path | None = None) -> None:
    print("+ " + " ".join(str(a) for a in args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def digest(*paths: Path) -> str:
    value = hashlib.sha256()
    for path in paths:
        value.update(path.read_bytes())
    return value.hexdigest()


def setup_python() -> None:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Python 3.12 is required by the pinned OCR packages. Install it, then start again.")
    version = None
    if PYTHON.is_file():
        probe = subprocess.run([str(PYTHON), "--version"], capture_output=True, text=True, check=False)
        version = (probe.stdout or probe.stderr).strip()
    if not version or not version.startswith("Python 3.12."):
        if VENV.resolve() != (BACKEND / ".venv").resolve() or not VENV.resolve().is_relative_to(BACKEND.resolve()):
            raise RuntimeError("Refusing to replace a Python environment outside the app backend.")
        other = VENV / ("bin/python" if os.name == "nt" else "Scripts/python.exe")
        if other.exists() and not PYTHON.exists():
            # A folder shared with another system (an AI app's sandbox, a synced drive) holds that
            # system's environment. Clearing it would break the app on the computer that installed it.
            raise SystemExit("career-dashboard/backend/.venv was built for another operating system. Run the "
                             "launcher on the computer that installed it, or install this copy in its own folder.")
        print("[1/3] Creating the private Python 3.12 environment...", flush=True)
        run([sys.executable, "-m", "venv", *(["--clear"] if VENV.exists() else []), str(VENV)])
    requirements = BACKEND / "requirements.txt"
    stamp = VENV / ".career-requirements.sha256"
    wanted = digest(requirements)
    if not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != wanted:
        print("[2/3] Installing backend packages...", flush=True)
        run([str(PYTHON), "-m", "pip", "install", "-r", str(requirements)])
        stamp.write_text(wanted + "\n", encoding="ascii")


def setup_frontend() -> None:
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise SystemExit("Node.js and npm are required. Install Node.js 20 or newer, then start again.")
    result = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    major = int(result.stdout.strip().lstrip("v").split(".")[0])
    if major < 20:
        raise SystemExit(f"Node.js 20 or newer is required; found {result.stdout.strip()}.")
    stamp = FRONTEND / "node_modules/.career-lock.sha256"
    wanted = digest(FRONTEND / "package.json", FRONTEND / "package-lock.json")
    if not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != wanted:
        print("[3/3] Installing dashboard packages...", flush=True)
        run([npm, "ci"], cwd=FRONTEND)
        stamp.write_text(wanted + "\n", encoding="ascii")


def tectonic_asset(system: str | None = None, machine: str | None = None) -> tuple[str, str] | None:
    """The pinned release asset (name, sha256) for this computer, or None for an unsupported one."""
    import platform

    system = (system or platform.system()).casefold()
    machine = (machine or platform.machine()).casefold()
    arch = "x86_64" if machine in {"amd64", "x86_64", "x64"} else "aarch64" if machine in {"arm64", "aarch64"} else machine
    if system == "windows":
        arch = "x86_64"  # Windows on Arm runs the x64 build
    return TECTONIC_ASSETS.get((system, arch))


def install_tectonic(download=None) -> Path | None:
    """Fetch the pinned Tectonic into backend/.tools when none is installed; returns its path.

    Runs in the app's own Python, so HTTPS uses the operating system's trust store (backend/__init__.py).
    The archive is checked against the pinned SHA-256 before anything is extracted, and only the
    tectonic binary itself is taken out of it.
    """
    import io
    import tarfile
    import zipfile

    sys.path.insert(0, str(APP))
    from backend.pdf_compiler import tectonic_executable

    if tectonic_executable():
        return None
    asset = tectonic_asset()
    if not asset:
        print("  Tectonic: no pinned build for this computer; install it from https://tectonic-typesetting.github.io",
              flush=True)
        return None
    name, expected = asset
    url = TECTONIC_URL.format(version=TECTONIC_VERSION, name=name)
    print(f"  Downloading Tectonic {TECTONIC_VERSION} for resume PDFs...", flush=True)
    if download is None:
        from urllib.request import Request, urlopen

        def download(address):
            with urlopen(Request(address, headers={"User-Agent": "CareerWorkspace-launcher"}), timeout=180) as response:
                return response.read(80_000_000)
    data = download(url)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("The Tectonic download did not match its published SHA-256; nothing was installed.")
    binary = "tectonic.exe" if name.endswith(".zip") else "tectonic"
    target = TOOLS / f"tectonic-{TECTONIC_VERSION}" / binary
    target.parent.mkdir(parents=True, exist_ok=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member = next(m for m in archive.namelist() if Path(m).name == binary)
            target.write_bytes(archive.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = next(m for m in archive.getmembers() if m.isfile() and Path(m.name).name == binary)
            target.write_bytes(archive.extractfile(member).read())
        target.chmod(0o755)
    return target


def report_tools() -> None:
    sys.path.insert(0, str(APP))
    from backend.ai import ready_providers
    from backend.paths import APP_ROOT
    from backend.pdf_compiler import tectonic_executable

    ready = ready_providers(APP_ROOT)
    available = [name for name, ok in ready.items() if ok]
    print("\nLocal tool check:", flush=True)
    print("  Python: " + sys.version.split()[0], flush=True)
    print("  Node.js: " + subprocess.check_output(["node", "--version"], text=True).strip(), flush=True)
    print("  Tectonic: " + ("ready" if tectonic_executable() else "missing; resume PDFs need it"), flush=True)
    system_ocr = bool(shutil.which("pdftoppm") and shutil.which("tesseract"))
    python_ocr = (importlib.util.find_spec("rapidocr_onnxruntime") is not None
                  and importlib.util.find_spec("pypdfium2") is not None)
    ocr_status = ("ready (Poppler and Tesseract)" if system_ocr else
                  "ready (Python OCR)" if python_ocr else
                  "missing; install the Python requirements or Poppler and Tesseract")
    print("  Scanned PDF OCR: " + ocr_status, flush=True)
    print("  AI: " + (", ".join(available) if available else "no configured provider; add a key or sign in to a supported CLI"), flush=True)
    print("  Setup: see README.md for installation links and provider setup.\n", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true", help="Install and check dependencies, then exit")
    parser.add_argument("--_report-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_tectonic", action="store_true", help=argparse.SUPPRESS)
    args, server_args = parser.parse_known_args()
    try:
        if args._report_only:
            report_tools()
            return 0
        if args._tectonic:
            installed = install_tectonic()
            if installed:
                print(f"  Tectonic installed in {installed.parent}", flush=True)
            return 0
        setup_python()
        setup_frontend()
        try:
            run([str(PYTHON), str(Path(__file__).resolve()), "--_tectonic"])
        except subprocess.CalledProcessError:
            print("  Tectonic could not be installed automatically; resume PDFs need it (see README.md).", flush=True)
        run([str(PYTHON), str(Path(__file__).resolve()), "--_report-only"])
        if not args.preflight_only:
            run([str(PYTHON), str(BACKEND / "run.py"), *server_args], cwd=BACKEND)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Setup failed: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
