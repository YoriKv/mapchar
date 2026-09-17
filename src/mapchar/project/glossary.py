"""The project's glossary: terms of the game and how they are translated.

No Qt: the project file stores it, the Glossary window edits it, and the
strings on screen are matched against it here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mapchar.core.text import fold


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    """What the original says: a name, an item, a phrase."""
    translation: str = ""
    """What it is to become, everywhere."""
    notes: str = ""


def matching_terms(terms: Iterable[GlossaryTerm], text: str) -> list[GlossaryTerm]:
    """The terms ``text`` holds, case and form folded, longest term first."""
    hay = fold(text)
    found = [t for t in terms if t.term and fold(t.term) in hay]
    return sorted(found, key=lambda t: (-len(t.term), fold(t.term)))


def glossary_dicts(terms: Iterable[GlossaryTerm]) -> list[dict[str, str]]:
    """The glossary as the project file writes it."""
    out = []
    for t in terms:
        d = {"t": t.term, "r": t.translation}
        if t.notes:
            d["n"] = t.notes
        out.append(d)
    return out


def glossary_from(raw: Any) -> list[GlossaryTerm]:
    """The glossary a project file holds; anything malformed is left out."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        term = item.get("t")
        if not isinstance(term, str):
            continue
        out.append(
            GlossaryTerm(
                term, str(item.get("r", "") or ""), str(item.get("n", "") or "")
            )
        )
    return out


__all__ = ["GlossaryTerm", "glossary_dicts", "glossary_from", "matching_terms"]
