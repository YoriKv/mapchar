"""Matching a typed filter against the fields of a row.

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
