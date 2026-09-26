#!/usr/bin/env python3
"""Run existing validators with local Tectonic files and writable Swift caches."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
runtime = HERE / '.runtime'
bundle = runtime / 'tectonic-bundle'
if not (bundle / 'SHA256SUM').exists():
    cache = Path.home() / 'Library/Caches/Tectonic/bundles/data'
    candidates = [p for p in (cache.iterdir() if cache.exists() else []) if p.is_dir() and (p / 'tectonic-format-latex.tex').exists()]
    if not candidates:
        raise SystemExit('No complete local Tectonic cache found; restore dependencies before building resumes.')
    source = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    shutil.copytree(source, bundle, dirs_exist_ok=True)
    (bundle / 'SHA256SUM').write_text(source.name + '\n')
bin_dir = runtime / 'bin'
bin_dir.mkdir(parents=True, exist_ok=True)
search_path = os.pathsep.join(p for p in os.environ.get('PATH', '').split(os.pathsep) if Path(p).resolve() != bin_dir.resolve())
compiler = shutil.which('tectonic', path=search_path)
if not compiler:
    raise SystemExit('Tectonic is not installed.')
wrapper = bin_dir / 'tectonic'
import shlex
wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(compiler) + ' -b ' + shlex.quote(str(bundle)) + ' "$@"\n')
wrapper.chmod(0o755)
env = os.environ.copy()
env['PATH'] = str(bin_dir) + os.pathsep + env.get('PATH', '')
for key, folder in [('CLANG_MODULE_CACHE_PATH', 'clang-cache'), ('SWIFT_MODULECACHE_PATH', 'swift-cache')]:
    location = runtime / folder
    location.mkdir(parents=True, exist_ok=True)
    env[key] = str(location)
if len(sys.argv) < 2:
    raise SystemExit('Usage: with_resume_runtime.py COMMAND [ARGS...]')
raise SystemExit(subprocess.call(sys.argv[1:], env=env))
