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


def nfc(text: str) -> str:
    """``text`` composed: the one form mapchar compares and stores."""
    return unicodedata.normalize("NFC", text)


def same_text(a: str, b: str) -> bool:
    """Whether two script texts say the same thing.

    Composed, so the spelling of a character does not matter, and line breaks
    aside: a translation is laid out to its box, and where its lines fall is
    not what it says.
    """
    return nfc(a).replace("\n", "") == nfc(b).replace("\n", "")


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
    out: list[str] = []
    for ch in text:
        if out and is_mark(ch):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def units(text: str) -> list[str]:
    """``text`` composed and split into glyph slots: :func:`graphemes` of
    :func:`nfc`, which is how every layer counts and compares characters."""
    return graphemes(nfc(text))


def fold(text: str) -> str:
    """``text`` for a case-insensitive, form-insensitive comparison.

    Never index a folded string with offsets from the unfolded one: folding
    changes lengths (``İ`` folds to two characters). Fold each piece instead.
    """
    return nfc(text).casefold()
