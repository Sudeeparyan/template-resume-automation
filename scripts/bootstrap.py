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
    args, server_args = parser.parse_known_args()
    try:
        if args._report_only:
            report_tools()
            return 0
        setup_python()
        setup_frontend()
        run([str(PYTHON), str(Path(__file__).resolve()), "--_report-only"])
        if not args.preflight_only:
            run([str(PYTHON), str(BACKEND / "run.py"), *server_args], cwd=BACKEND)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Setup failed: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
