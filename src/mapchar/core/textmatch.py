"""Finding things: a typed filter over the fields of a row, and a needle in a
buffer.

Every list in the app filters the same way: the text typed is split into
words, and a row is shown when each of them appears somewhere in it. The
words stand on their own, so order does not matter and ``sword fish`` finds
a row holding both.
"""

from __future__ import annotations

from collections.abc import Sequence

from mapchar.core.text import fold


def words_of(query: str) -> list[str]:
    """``query`` as the folded words a filter asks for.

    Empty when nothing was typed, which :func:`matches_words` reads as every
    row matching — and which a caller that only expands or scrolls on a real
    filter tests for itself.
    """
    return fold(query).split()


def matches_words(words: Sequence[str], *fields: str) -> bool:
    """Whether every word of ``words`` is in one of ``fields``.

    Each field is folded on its own, never a join of them, so no word is
    matched across a boundary between two of them.
    """
    if not words:
        return True
    folded = [fold(f) for f in fields]
    return all(any(word in f for f in folded) for word in words)


def next_match(
    data: bytes,
    needle: bytes,
    lo: int,
    hi: int,
    at: int,
    *,
    backwards: bool = False,
    on_match: bool = False,
) -> int | None:
    """The nearest match to ``at`` within ``data[lo:hi]``, wrapping round.

    Wrapping is what makes a search from the middle reach the matches behind
    it, and ``lo`` and ``hi`` are what keep it to the stretch being looked at
    rather than the whole buffer.

    ``on_match`` says ``at`` is where a match already sits, and so is where a
    forward search starts *past*: without it a match right at ``at`` is the
    first one, not the last.
    """
    if not needle:
        return None
    at = min(max(at, lo), hi)
    if backwards:
        pos = data.rfind(needle, lo, at)
        if pos < 0:
            pos = data.rfind(needle, lo, hi)  # wrap to the last match
    else:
        pos = data.find(needle, at + 1 if on_match else at, hi)
        if pos < 0:
            pos = data.find(needle, lo, hi)  # wrap to the first
    return pos if pos >= 0 else None
