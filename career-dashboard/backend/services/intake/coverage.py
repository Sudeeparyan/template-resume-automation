"""The "miss nothing" ledger for an intake.

Every block of every uploaded document ends in exactly one of three states:
  cited      an extracted fact (or a fact the auditor recovered) cites it
  narrative  the extractor judged it to state no new fact (reflection, transitions)
  verbatim   neither: the block is copied word for word into 09-anything-else.md

So nothing the person wrote is dropped, and the review screen can say
"461 of 461 blocks accounted for". Separately, every number in the documents is
looked for in the draft; numbers that appear nowhere are listed so the person can
see them before building.
"""

from __future__ import annotations

import json
import re

_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*%?")
# Numbering and years that say nothing on their own.
_TRIVIAL = re.compile(r"^(?:\d|1[0-2])$")


def _cited(value, found: set) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "refs" and isinstance(item, list):
                found.update(str(r).strip("[] ") for r in item)
            else:
                _cited(item, found)
    elif isinstance(value, list):
        for item in value:
            _cited(item, found)


def ledger(blocks: list[dict], draft: dict) -> dict:
    ids = [b["id"] for b in blocks]
    cited: set = set()
    _cited({k: v for k, v in draft.items() if k not in {"narrative_only", "coverage"}}, cited)
    narrative = {str(i).strip("[] ") for i in draft.get("narrative_only") or []} - cited
    verbatim = [i for i in ids if i not in cited and i not in narrative]
    text = json.dumps({k: v for k, v in draft.items() if k != "coverage"}, ensure_ascii=False)
    unused = []
    for block in blocks:
        for number in _NUMBER.findall(block["text"]):
            if _TRIVIAL.match(number) or number in text:
                continue
            unused.append({"number": number, "block": block["id"],
                           "context": block["text"][max(0, block["text"].find(number) - 60): block["text"].find(number) + 60]})
    return {
        "blocks": len(ids),
        "cited": len([i for i in ids if i in cited]),
        "narrative": len([i for i in ids if i in narrative]),
        "verbatim": verbatim,
        "accounted": len(ids),
        "numbers_not_used": unused,
    }


def verbatim_section(blocks: list[dict], verbatim_ids: list[str]) -> str:
    """The Markdown kept in 09-anything-else.md for blocks no fact cites."""
    wanted = set(verbatim_ids)
    lines = []
    for block in blocks:
        if block["id"] in wanted:
            lines.append(f"- [{block['id']}] {block['text']}")
    return "\n".join(lines)
