"""Find Tectonic, including installations made after the dashboard started."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def tectonic_executable() -> str | None:
    """Return the installed compiler without caching a stale Windows PATH."""
    found = shutil.which("tectonic")
    if found or os.name != "nt":
        return found

    # Windows does not update the environment of programs already running when
    # an installer adds a directory to PATH. Read the current registry values
    # so the open dashboard can use a newly installed compiler immediately.
    import winreg

    paths = []
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(hive, key) as registry:
                value, _ = winreg.QueryValueEx(registry, "Path")
                paths.append(os.path.expandvars(value))
        except (FileNotFoundError, OSError):
            continue
    paths.append(str(Path.home() / ".local" / "bin"))
    paths.append(str(Path.home() / "scoop" / "shims"))
    return shutil.which("tectonic", path=os.pathsep.join(paths))
