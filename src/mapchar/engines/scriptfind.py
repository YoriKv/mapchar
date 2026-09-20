"""Code-aware find and replace over script text.

Matching runs over the pieces the script grammar makes
(:func:`~mapchar.core.tokens.piece_spans`), not over characters:
a ``[...]`` code is **one piece** however many characters it spells, and an
escape is one piece too. So ``[line]`` in a needle matches the code and never
the letters inside it, and a needle of plain letters never matches part of a
code — a plain substring replace would turn ``[line]`` into ``[lene]``.
"""

from __future__ import annotations

from mapchar.core.text import fold, nfc
from mapchar.core.tokens import piece_spans


def _pieces(text: str, case: bool) -> list[tuple[int, int, str]]:
    """Every piece of ``text`` as ``(start, stop, what to compare)``.

    Each piece is folded on its own: case folding changes lengths (``İ`` folds
    to two characters), so a folded whole string can never be indexed with the
    spans of the original.
    """
    spans = piece_spans(text)
    if case:
        return [(a, b, text[a:b]) for a, b in spans]
    return [(a, b, fold(text[a:b])) for a, b in spans]


def find(
    text: str, needle: str, *, case: bool = True, start: int = 0
) -> tuple[int, int] | None:
    """Where ``needle`` occurs in ``text`` as whole pieces, at or after ``start``.

    Returns the match's ``(start, stop)`` character offsets, or None. The
    needle is normalised to NFC, as every text mapchar keeps is.
    """
    hay = _pieces(text, case)
    pattern = [p[2] for p in _pieces(nfc(needle), case)]
    if not pattern:
        return None
    for i in range(len(hay) - len(pattern) + 1):
        if hay[i][0] < start:
            continue
        if [p[2] for p in hay[i : i + len(pattern)]] == pattern:
            return hay[i][0], hay[i + len(pattern) - 1][1]
    return None


def contains(text: str, needle: str, *, case: bool = True) -> bool:
    return find(text, needle, case=case) is not None


def replace(
    text: str, needle: str, replacement: str, *, case: bool = True
) -> tuple[str, int]:
    """Every whole-piece occurrence replaced; the text and how many went."""
    out: list[str] = []
    at, count = 0, 0
    while True:
        span = find(text, needle, case=case, start=at)
        if span is None:
            break
        out.append(text[at : span[0]])
        out.append(replacement)
        at = span[1]
        count += 1
    out.append(text[at:])
    return "".join(out), count


__all__ = ["contains", "find", "replace"]
