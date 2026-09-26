"""Who the agents are working for, in the words their instructions need.

Every profile gets a persona built from
its own profile, country pack and resume contract. Pronouns are never guessed:
a persona speaks of the candidate by name or as "they".
"""

from __future__ import annotations

from pathlib import Path

import yaml


def persona_for(root) -> dict | None:
    root = Path(root)
    from backend.countries import pack_for
    from backend.resume_contract import contract_for

    try:
        profile = yaml.safe_load((root / "data/config/profile.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        profile = {}
    candidate = profile.get("candidate") or {}
    saved = profile.get("persona") or {}
    pack = pack_for(root)
    try:
        contract = contract_for(root)
        pages = contract.pages
    except Exception:  # noqa: BLE001 - a half-built profile still gets sensible wording
        pages = 1
    name = (str(saved.get("name") or candidate.get("preferred_name") or "").strip()
            or str(candidate.get("full_name") or "the candidate").split()[0])
    shape = f"one {pack.paper_label} page" if pages == 1 else f"{pages} {pack.paper_label} pages"
    reapply_rule = "exact posting identities are deduplicated; status is never inferred from silence"
    return {
        "candidate": name,
        "full_name": str(candidate.get("full_name") or name),
        "situation": str(saved.get("situation") or f"looking for {pack.text('market')}"),
        "country": pack.name,
        "gate_rule": str(saved.get("gate_rule") or pack.text("gate_rule")),
        "resume_shape": shape,
        "paper_label": pack.paper_label,
        "spelling": pack.spelling,
        "reapply_rule": reapply_rule,
    }
