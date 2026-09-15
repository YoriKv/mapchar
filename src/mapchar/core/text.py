"""The Unicode model every layer shares: form, graphemes and folding.

Every text mapchar keeps — table entries, translations, font alphabets, search
needles — is **NFC**: composed, so ``が`` is one code point whatever the file
spelled. Comparison is then a string comparison, and a grapheme is a base
character plus the combining marks that follow it, which is what a glyph slot
and a table entry stand for.

How a text *file* is spelled — its encoding, line endings and backslash
escapes — is :mod:`mapchar.project.formats.textfile`'s.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache


def nfc(text: str) -> str:
    """``text`` composed: the one form mapchar compares and stores."""
    return unicodedata.normalize("NFC", text)


def nfd(text: str) -> str:
    """``text`` decomposed: what the encoder splits into atoms, so a composed
    ``が`` and a table spelling it ``か`` plus a dakuten meet."""
    return unicodedata.normalize("NFD", text)


def is_mark(ch: str) -> bool:
    """Whether ``ch`` is a combining mark, which joins the character before it."""
    return unicodedata.category(ch).startswith("M")


def graphemes(text: str) -> list[str]:
    """``text`` as base-plus-combining-marks units: one glyph slot each.

    Not the full grapheme-cluster algorithm — no emoji sequences, no Hangul
    jamo joining — but enough for the decomposed kana legacy tables carry: a
    dakuten never becomes a unit of its own.
    """
    units: list[str] = []
    for ch in text:
        if units and is_mark(ch):
            units[-1] += ch
        else:
            units.append(ch)
    return units


@lru_cache(maxsize=32)
def char_units(chars: str) -> tuple[str, ...]:
    """:func:`graphemes` of NFC ``chars``, cached for a font's alphabet."""
    return tuple(graphemes(nfc(chars)))


def fold(text: str) -> str:
    """``text`` for a case-insensitive, form-insensitive comparison.

    Never index a folded string with offsets from the unfolded one: folding
    changes lengths (``İ`` folds to two characters). Fold each piece instead.
    """
    return nfc(text).casefold()
