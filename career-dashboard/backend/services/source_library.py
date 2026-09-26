"""Versioned, profile-local source documents used to build a candidate workspace.

The library owns immutable uploaded versions. ``data/context/files`` remains the
intake's current-source projection, so the old intake and setup-chat routes keep
using the same extractor. No source or index is shared between profiles.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.services.intake.extract import MAX_BYTES, SUPPORTED, Block, source_markdown


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    stem = Path(str(name or "document")).name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .") or "document"
    return stem[:120]


class SourceLibrary:
    """Small atomic JSON manifest and immutable bytes for one profile."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.directory = self.root / "data/source_library"
        self.manifest = self.directory / "sources.json"
        self.files_dir = self.root / "data/context/files"
        self.lock = threading.RLock()

    def _read(self) -> list[dict]:
        if not self.manifest.is_file():
            return []
        return json.loads(self.manifest.read_text(encoding="utf-8")).get("sources", [])

    def _write(self, sources: list[dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / ("sources-" + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "sources": sources}, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        temporary.replace(self.manifest)

    def _version_path(self, source: dict, version: int) -> Path:
        return self.directory / "files" / source["id"] / f"v{version:04d}{Path(source['name']).suffix.lower()}"

    def _find(self, sources: list[dict], source_id: str) -> dict:
        for source in sources:
            if source["id"] == source_id:
                return source
        raise ValueError("No such source in this profile.")

    def import_existing(self) -> None:
        """Adopt older intake uploads once, without changing their current file bytes."""
        with self.lock:
            known = {s["name"].casefold() for s in self._read()}
            if not self.files_dir.is_dir():
                return
            for path in sorted(self.files_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in SUPPORTED and path.name.casefold() not in known:
                    self.add_upload(path.name, path.read_bytes(), materialize=False)
                    known.add(path.name.casefold())

    def list(self) -> list[dict]:
        self.import_existing()
        with self.lock:
            return [dict(source) for source in self._read()]

    def add_upload(self, name: str, data: bytes, *, kind: str = "file", materialize: bool = True) -> dict:
        name = safe_name(name)
        if Path(name).suffix.lower() not in SUPPORTED:
            raise ValueError(f"{name}: upload a Word (.docx), PDF, text (.txt) or Markdown (.md) file.")
        if not data:
            raise ValueError(f"{name} is empty.")
        if len(data) > MAX_BYTES:
            raise ValueError(f"{name} is larger than 20 MB. Upload a smaller copy.")
        with self.lock:
            sources = self._read()
            source = next((s for s in sources if s["name"].casefold() == name.casefold()), None)
            if source is None:
                source = {"id": uuid.uuid4().hex[:16], "name": name, "kind": kind, "active": True,
                          "current_version": 0, "versions": [], "preview": ""}
                sources.append(source)
            version = source["current_version"] + 1
            path = self._version_path(source, version)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            metadata = {"version": version, "created_at": _now(), "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(), "extracted_chars": 0}
            source.update(kind=kind, active=True, current_version=version)
            source["versions"].append(metadata)
            if kind == "note":
                source["preview"] = data.decode("utf-8", errors="replace")[:300]
            self._write(sources)
            if materialize:
                self.files_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, self.files_dir / source["name"])
            return dict(source)

    def add_note(self, name: str, text: str) -> dict:
        body = str(text or "").strip()
        if not body:
            raise ValueError("Write a note before saving it as a source.")
        name = safe_name(name or "Personal notes")
        if Path(name).suffix.lower() not in {".md", ".txt"}:
            name += ".md"
        return self.add_upload(name, (body + "\n").encode("utf-8"), kind="note")

    def add_version(self, source_id: str, data: bytes) -> dict:
        with self.lock:
            source = self._find(self._read(), source_id)
            return self.add_upload(source["name"], data, kind=source["kind"])

    def set_active(self, source_id: str, active: bool) -> dict:
        with self.lock:
            sources = self._read()
            source = self._find(sources, source_id)
            source["active"] = bool(active)
            self._write(sources)
            target = self.files_dir / source["name"]
            if active:
                self.files_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self._version_path(source, source["current_version"]), target)
            else:
                target.unlink(missing_ok=True)
            return dict(source)

    def active_snapshot(self) -> list[dict]:
        return [{"source_id": s["id"], "name": s["name"], "version": s["current_version"],
                 "sha256": s["versions"][-1]["sha256"]} for s in self.list() if s["active"]]

    def capture_extraction(self, blocks: list[Block]) -> None:
        """Persist cited blocks for each immutable version and refresh the search index."""
        with self.lock:
            sources = self._read()
            index = []
            today = datetime.now().date().isoformat()
            for source in sources:
                if not source["active"]:
                    continue
                chosen = [b for b in blocks if b.source == source["name"]]
                directory = self._version_path(source, source["current_version"]).parent
                version = source["current_version"]
                (directory / f"v{version:04d}.blocks.json").write_text(
                    json.dumps([b.as_dict() for b in chosen], ensure_ascii=False, indent=2), encoding="utf-8")
                (directory / f"v{version:04d}.md").write_text(
                    source_markdown(blocks, source["name"], today), encoding="utf-8")
                source["versions"][-1]["extracted_chars"] = sum(len(b.text) for b in chosen)
                source["preview"] = " ".join(b.text for b in chosen)[:300]
                index.extend({"block_id": b.id, "source_id": source["id"], "version": version,
                              "name": source["name"], "kind": b.kind, "text": b.text} for b in chosen)
            (self.directory / "index.json").write_text(
                json.dumps({"built_at": _now(), "blocks": index}, ensure_ascii=False, indent=2), encoding="utf-8")
            self._write(sources)

    def search(self, query: str, limit: int = 8) -> list[dict]:
        path = self.directory / "index.json"
        if not path.is_file():
            return []
        words = [w.casefold() for w in re.findall(r"[\w-]+", query) if len(w) > 2]
        blocks = json.loads(path.read_text(encoding="utf-8")).get("blocks", [])
        return sorted((b for b in blocks if any(w in b["text"].casefold() for w in words)),
                      key=lambda b: -sum(b["text"].casefold().count(w) for w in words))[:limit]
