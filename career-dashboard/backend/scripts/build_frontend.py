#!/usr/bin/env python3
"""Build the React client with Node and the npm lockfile."""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOMEBREW = Path("/opt/homebrew/bin")
CODEX_RUNTIME = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"


def _tool(name, *fallbacks):
    found = shutil.which(name)
    if found:
        return found
    for candidate in fallbacks:
        if candidate.exists():
            return str(candidate)
    return None


def build(force=False):
    frontend = ROOT / "frontend"
    sources = [
        *frontend.glob("src/**/*"),
        frontend / "package.json",
        frontend / "package-lock.json",
        frontend / "index.html",
        frontend / "vite.config.ts",
    ]
    output = frontend / "dist/index.html"
    if (
        not force
        and output.exists()
        and all(
            p.stat().st_mtime <= output.stat().st_mtime for p in sources if p.is_file()
        )
    ):
        return
    node = _tool("node", HOMEBREW / "node", CODEX_RUNTIME / "node/bin/node")
    if not node:
        raise SystemExit("Building the dashboard needs Node.js: brew install node")
    env = {
        **os.environ,
        "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    if not (frontend / "node_modules").exists():
        npm = _tool("npm", HOMEBREW / "npm")
        if not npm:
            raise SystemExit("Installing frontend dependencies needs npm: brew install node")
        subprocess.run([npm, "ci"], cwd=frontend, env=env, check=True)
    subprocess.run(
        [node, str(frontend / "node_modules/typescript/bin/tsc"), "-b"],
        cwd=frontend,
        env=env,
        check=True,
    )
    subprocess.run(
        [node, str(frontend / "node_modules/vite/bin/vite.js"), "build"],
        cwd=frontend,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    build(force=True)
