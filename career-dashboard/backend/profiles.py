"""Profiles: one candidate each, kept completely apart.

Every profile is a folder ``profiles/<id>/`` with the same layout
(``data/config``, ``data/context``, ``data/templates``, ``data/output``,
``data/career.db``) plus its own ``daily-job-search/``. Nothing is shared
between profiles except the code, the machine's AI keys and sign-ins, and
public reference data (the USCIS employer list for US searches).

A profile is ``onboarding`` until its documents have been read and its
workspace built, then ``ready``. The registry (``profiles/registry.json``)
records id, display name, country pack, state, lock and dates; everything a
profile knows lives in its own folder.
"""

from __future__ import annotations

import gc
import json
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.paths import APP_ROOT, PROFILES

ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
STATES = {"onboarding", "ready"}
MARKETS = {"ie", "us"}


class ProfileError(ValueError):
    """A refused profile operation; the message is shown to the person as is."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(name: str) -> str:
    """'Example Person' -> 'example-person' (ASCII, at most 40 characters)."""
    text = re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")
    return text[:40].strip("-") or "profile"


def initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", str(name or "").strip()) if p]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


class ProfileStore:
    """The registry of profiles and where each one's workspace lives.

    ``base`` holds private profile folders and the registry. ``legacy_root`` is
    retained only to read an explicitly registered pre-template workspace.
    """

    def __init__(self, base: Path = PROFILES, legacy_root: Path = APP_ROOT):
        self.base = Path(base)
        self.legacy_root = Path(legacy_root)
        self.registry = self.base / "registry.json"
        self.trash = self.base / ".trash"
        self._lock = threading.RLock()

    # ---- registry file --------------------------------------------------
    def _read(self) -> dict:
        data = {}
        if self.registry.is_file():
            # Windows can hold the file for a moment (a replace in progress, a virus scan).
            # Never read that as "no profiles": the next write would drop every one of them.
            for attempt in range(6):
                try:
                    data = json.loads(self.registry.read_text(encoding="utf-8")) or {}
                    break
                except (OSError, json.JSONDecodeError):
                    if attempt == 5:
                        raise ProfileError("The profile list is busy. Try again in a moment.")
                    time.sleep(0.1 * (attempt + 1))
        profiles = [p for p in data.get("profiles") or [] if isinstance(p, dict) and ID_PATTERN.match(str(p.get("id", "")))]
        for profile in profiles:
            markets = profile.get("target_markets") or [profile.get("country") or "ie"]
            profile["target_markets"] = [m for m in markets if m in MARKETS] or ["ie"]
            profile["country"] = profile["target_markets"][0]
            profile.setdefault("work_authorization_by_market", {})
        ids = {p["id"] for p in profiles}
        last_used = data.get("last_used")
        return {"profiles": profiles, "last_used": last_used if last_used in ids else (profiles[0]["id"] if profiles else "")}

    def _write(self, data: dict) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.base, delete=False) as handle:
            handle.write(text)
            temp = Path(handle.name)
        for attempt in range(6):
            try:
                temp.replace(self.registry)
                return
            except PermissionError:
                if attempt == 5:
                    temp.unlink(missing_ok=True)
                    raise
                time.sleep(0.3 * (attempt + 1))

    # ---- queries ----------------------------------------------------------
    def list(self) -> list[dict]:
        with self._lock:
            return [self._public(p) for p in self._read()["profiles"]]

    def last_used(self) -> str:
        with self._lock:
            return self._read()["last_used"]

    def get(self, profile_id: str) -> dict:
        with self._lock:
            for profile in self._read()["profiles"]:
                if profile["id"] == profile_id:
                    return self._public(profile)
        raise ProfileError("No such profile. Pick one from the profile menu.")

    def exists(self, profile_id: str) -> bool:
        try:
            self.get(profile_id)
            return True
        except ProfileError:
            return False

    def root_for(self, profile_id: str) -> Path:
        """The workspace root (the folder holding data/) of a profile."""
        profile = self.get(profile_id)
        return self.legacy_root if profile.get("legacy") else self.base / profile_id

    def _public(self, profile: dict) -> dict:
        return {**profile, "initials": initials(profile.get("name", "")), "locked": bool(profile.get("locked"))}

    # ---- changes ----------------------------------------------------------
    def create(self, name: str) -> dict:
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        if not 1 <= len(name) <= 80:
            raise ProfileError("Give the new profile a name (up to 80 characters).")
        with self._lock:
            data = self._read()
            ids = {p["id"] for p in data["profiles"]}
            base_id = slug(name)
            profile_id, n = base_id, 2
            while profile_id in ids or (self.base / profile_id).exists():
                suffix = f"-{n}"
                profile_id = base_id[: 40 - len(suffix)].strip("-") + suffix
                n += 1
            self.skeleton(self.base / profile_id)
            entry = {"id": profile_id, "name": name, "country": "ie", "target_markets": ["ie"],
                     "work_authorization_by_market": {}, "state": "onboarding", "locked": False,
                     "created_at": _now()}
            data["profiles"].append(entry)
            self._write(data)
            return self._public(entry)

    @staticmethod
    def skeleton(root: Path) -> None:
        """An empty workspace: the folders a profile's files will go into, nothing else."""
        for sub in ("data/config", "data/context/files", "data/context/sources", "data/templates",
                    "data/output/applications", "data/interview-prep", "daily-job-search"):
            (root / sub).mkdir(parents=True, exist_ok=True)

    def update(self, profile_id: str, **fields) -> dict:
        with self._lock:
            data = self._read()
            for profile in data["profiles"]:
                if profile["id"] == profile_id:
                    if profile.get("legacy"):
                        fields = {k: v for k, v in fields.items() if k in {"name"}}
                    if "state" in fields and fields["state"] not in STATES:
                        raise ProfileError("Unknown profile state")
                    if "target_markets" in fields:
                        markets = fields["target_markets"]
                        if not isinstance(markets, list) or not markets or any(m not in MARKETS for m in markets):
                            raise ProfileError("Choose Ireland, the US, or both.")
                        fields["target_markets"] = list(dict.fromkeys(markets))
                        fields["country"] = fields["target_markets"][0]
                    profile.update(fields, updated_at=_now())
                    self._write(data)
                    return self._public(profile)
        raise ProfileError("No such profile. Pick one from the profile menu.")

    def mark_used(self, profile_id: str) -> None:
        with self._lock:
            data = self._read()
            if any(p["id"] == profile_id for p in data["profiles"]) and data.get("last_used") != profile_id:
                data["last_used"] = profile_id
                self._write(data)

    def check_confirmation(self, profile_id: str, confirm: str) -> dict:
        """Refuse a reset or delete of a locked profile, or one confirmed with the wrong name."""
        profile = self.get(profile_id)
        if profile.get("locked"):
            raise ProfileError(f"{profile['name']} is the locked default profile. It cannot be reset or deleted.")
        if re.sub(r"\s+", " ", str(confirm or "")).strip().casefold() != profile["name"].casefold():
            raise ProfileError(f"Type the profile name exactly ({profile['name']}) to confirm.")
        return profile

    def wipe(self, profile_id: str) -> None:
        """Erase a profile's folder for good. Windows may hold a file briefly, so the folder is
        first renamed into .trash (instantly out of use) and then removed, retrying; anything
        left is removed at the next start (purge_trash)."""
        profile = self.get(profile_id)
        if profile.get("locked") or profile.get("legacy"):
            raise ProfileError(f"{profile['name']} is the locked backup profile. It cannot be reset or deleted.")
        folder = self.base / profile_id
        if not folder.exists():
            return
        self.trash.mkdir(parents=True, exist_ok=True)
        target = self.trash / f"{profile_id}-{int(time.time() * 1000)}"
        for attempt in range(10):
            try:
                folder.rename(target)
                break
            except PermissionError:
                gc.collect()
                time.sleep(0.3 * (attempt + 1))
        else:
            raise ProfileError("Some of this profile's files are still in use. Close any open file from it and try again.")
        self._remove_tree(target)

    @staticmethod
    def _remove_tree(path: Path, attempts: int = 6) -> bool:
        for attempt in range(attempts):
            try:
                shutil.rmtree(path)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                gc.collect()
                time.sleep(0.3 * (attempt + 1))
        return False

    def purge_trash(self) -> None:
        if self.trash.is_dir():
            for leftover in self.trash.iterdir():
                self._remove_tree(leftover, attempts=2)

    def reset(self, profile_id: str) -> dict:
        """Back to zero: the folder is erased and recreated empty, ready for new documents."""
        with self._lock:
            profile = self.get(profile_id)
            self.wipe(profile_id)
            self.skeleton(self.base / profile_id)
            data = self._read()
            for entry in data["profiles"]:
                if entry["id"] == profile_id:
                    entry.update(state="onboarding", country="ie", target_markets=["ie"],
                                 work_authorization_by_market={}, reset_at=_now())
                    for key in ("built_at", "schedule"):
                        entry.pop(key, None)
            self._write(data)
            return self.get(profile["id"])

    def delete(self, profile_id: str) -> None:
        with self._lock:
            self.wipe(profile_id)
            data = self._read()
            data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
            if data.get("last_used") == profile_id:
                data["last_used"] = data["profiles"][0]["id"] if data["profiles"] else ""
            self._write(data)


_STORE: ProfileStore | None = None


def store() -> ProfileStore:
    global _STORE
    if _STORE is None:
        _STORE = ProfileStore()
    return _STORE
