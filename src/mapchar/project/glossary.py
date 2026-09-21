"""The project's glossary: terms of the game and how they are translated.

No Qt: the project file stores it, the Glossary panel edits it, and the
strings on screen are matched against it here — which terms a text holds,
where, what it reads as with them translated, and which of them a translation
has left out.

Matching is code-aware (:mod:`mapchar.engines.scriptfind`): a term never
matches inside a ``[code]``.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from mapchar.core.text import fold
from mapchar.engines import scriptfind

TSV_HEADER = ("term", "translation", "notes", "match case", "whole word")
"""The columns a glossary goes out under, and the first row an import skips."""


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    """What the original says: a name, an item, a phrase."""
    translation: str = ""
    """What it is to become, everywhere."""
    notes: str = ""
    match_case: bool = False
    """Only where the case is the term's too."""
    whole_word: bool = False
    """Only where no letter or digit stands against either end: for a script
    that spaces its words, where ``Ann`` is not to be found in ``Annex``."""


@dataclass(frozen=True)
class TermHit:
    """One place a term stands in a text."""

    start: int
    stop: int
    term: GlossaryTerm


def _by_length(terms: Iterable[GlossaryTerm]) -> list[GlossaryTerm]:
    """Longest term first, so ``Fire`` never takes the front of ``Fire Sword``."""
    return sorted(
        (t for t in terms if t.term), key=lambda t: (-len(t.term), fold(t.term))
    )


def _is_word(char: str) -> bool:
    return char.isalnum() or char == "_"


def _spans(term: GlossaryTerm, text: str) -> list[tuple[int, int]]:
    """Everywhere ``term`` stands in ``text``, by its own matching options."""
    out = []
    at = 0
    while True:
        span = scriptfind.find(text, term.term, case=term.match_case, start=at)
        if span is None:
            return out
        start, stop = span
        walled = (start == 0 or not _is_word(text[start - 1])) and (
            stop >= len(text) or not _is_word(text[stop])
        )
        if walled or not term.whole_word:
            out.append(span)
            at = stop
        else:
            at = start + 1


def term_hits(
    terms: Iterable[GlossaryTerm], text: str, *, translated_only: bool = False
) -> list[TermHit]:
    """Where the terms stand in ``text``, in reading order and never
    overlapping: a longer term is placed before a shorter one gets the chance.

    ``translated_only`` leaves out the terms with no translation to put in.
    """
    taken: list[TermHit] = []
    # A folded substring is what every hit has, and costs no reading of the
    # text's codes: most terms are not in most strings.
    hay = fold(text)
    for term in _by_length(terms):
        if translated_only and not term.translation:
            continue
        if fold(term.term) not in hay:
            continue
        for start, stop in _spans(term, text):
            if all(stop <= hit.start or hit.stop <= start for hit in taken):
                taken.append(TermHit(start, stop, term))
    return sorted(taken, key=lambda hit: hit.start)


def matching_terms(terms: Iterable[GlossaryTerm], text: str) -> list[GlossaryTerm]:
    """The terms ``text`` holds, longest term first."""
    found = {hit.term: None for hit in term_hits(terms, text)}
    return _by_length(found)


def replace_terms(terms: Iterable[GlossaryTerm], text: str) -> tuple[str, int]:
    """``text`` with every translated term in it translated, and how many went."""
    hits = term_hits(terms, text, translated_only=True)
    out: list[str] = []
    at = 0
    for hit in hits:
        out += (text[at : hit.start], hit.term.translation)
        at = hit.stop
    out.append(text[at:])
    return "".join(out), len(hits)


def replace_hit(text: str, hit: TermHit) -> str:
    """``text`` with the one hit translated."""
    return text[: hit.start] + hit.term.translation + text[hit.stop :]


def missing_terms(
    terms: Iterable[GlossaryTerm], original: str, translation: str
) -> list[GlossaryTerm]:
    """The terms the original holds whose translation the translation lacks:
    where a name went in some other way than the glossary has it."""
    return [
        term
        for term in matching_terms(terms, original)
        if term.translation
        and not scriptfind.contains(translation, term.translation, case=False)
    ]


def term_uses(
    terms: Sequence[GlossaryTerm], originals: Iterable[str]
) -> dict[GlossaryTerm, int]:
    """How many of ``originals`` hold each term."""
    uses = dict.fromkeys(terms, 0)
    for text in originals:
        for term in matching_terms(terms, text):
            uses[term] += 1
    return uses


def merged_terms(
    existing: Iterable[GlossaryTerm], incoming: Iterable[GlossaryTerm]
) -> list[GlossaryTerm]:
    """``existing`` with ``incoming`` laid over it: a term already there takes
    the incoming translation, notes and options, and a new one goes last."""
    out = list(existing)
    at = {fold(t.term): i for i, t in enumerate(out)}
    for term in incoming:
        key = fold(term.term)
        if key in at:
            out[at[key]] = replace(term, term=out[at[key]].term)
        else:
            at[key] = len(out)
            out.append(term)
    return out


# --- the project file -------------------------------------------------------


def glossary_dicts(terms: Iterable[GlossaryTerm]) -> list[dict[str, Any]]:
    """The glossary as the project file writes it."""
    out = []
    for t in terms:
        d: dict[str, Any] = {"t": t.term, "r": t.translation}
        if t.notes:
            d["n"] = t.notes
        if t.match_case:
            d["c"] = True
        if t.whole_word:
            d["w"] = True
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
                term,
                str(item.get("r", "") or ""),
                str(item.get("n", "") or ""),
                bool(item.get("c")),
                bool(item.get("w")),
            )
        )
    return out


# --- a glossary shared between projects ---------------------------------------


def _flag(text: str) -> bool:
    return text.strip().lower() in ("1", "true", "yes", "y", "x")


def glossary_text(terms: Iterable[GlossaryTerm], delimiter: str = "\t") -> str:
    """The glossary as a TSV (or CSV) with a header row."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    writer.writerow(TSV_HEADER)
    for t in terms:
        writer.writerow(
            (
                t.term,
                t.translation,
                t.notes,
                "yes" if t.match_case else "",
                "yes" if t.whole_word else "",
            )
        )
    return out.getvalue()


def glossary_from_text(text: str, delimiter: str = "\t") -> list[GlossaryTerm]:
    """The terms a TSV (or CSV) holds: term, translation, notes, match case,
    whole word — the first alone is enough. A header row and blank terms are
    passed over."""
    out = []
    for n, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter)):
        cells = [c.strip() for c in row] + [""] * len(TSV_HEADER)
        if not cells[0] or (n == 0 and fold(cells[0]) == TSV_HEADER[0]):
            continue
        out.append(
            GlossaryTerm(cells[0], cells[1], cells[2], _flag(cells[3]), _flag(cells[4]))
        )
    return out


__all__ = [
    "GlossaryTerm",
    "TSV_HEADER",
    "TermHit",
    "glossary_dicts",
    "glossary_from",
    "glossary_from_text",
    "glossary_text",
    "matching_terms",
    "merged_terms",
    "missing_terms",
    "replace_hit",
    "replace_terms",
    "term_hits",
    "term_uses",
]
