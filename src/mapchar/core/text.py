"""Text handling the file formats share: Unicode form, graphemes, line
splitting, backslash escapes, and reading a text file of unknown encoding.

Every text mapchar keeps — table entries, translations, font alphabets, search
needles — is **NFC**: composed, so ``が`` is one code point whatever the file
spelled. Comparison is then a string comparison, and a grapheme is a base
character plus the combining marks that follow it, which is what a glyph slot
and a table entry stand for.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_NAMED = {"\t": "\\t"}
"""Characters with a named escape, when they are escaped at all."""

_UNNAMED = {"n": "\n", "t": "\t"}

BOM = "﻿"

_ENCODINGS = ("utf-8-sig", "cp932", "latin-1")
"""Tried in order by :func:`read_text_any`; the last one always decodes."""


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


def read_text_any(path: str) -> tuple[str, str]:
    """The text of ``path`` and the encoding it was read as.

    UTF-8 (a BOM off) first, then ``cp932`` for the Shift-JIS tables romjuice
    and Cartographer projects are full of, then ``latin-1``, which decodes
    anything. The text is NFC and the caller can say what the encoding was.
    """
    with open(path, "rb") as f:
        data = f.read()
    for encoding in _ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-8-sig":
            encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
        return nfc(text), encoding
    raise AssertionError("latin-1 decodes every byte")  # pragma: no cover


def split_lines(text: str) -> list[str]:
    """The lines of a text file: a byte-order mark off, any line ending."""
    if text.startswith(BOM):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def escape(text: str, extra: str = "") -> str:
    """``\\`` and line breaks escaped, plus every character of ``extra``.

    A tab takes its named escape (``\\t``); anything else in ``extra`` keeps
    its own character behind a backslash.
    """
    out: list[str] = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch in extra:
            out.append(_NAMED.get(ch, "\\" + ch))
        else:
            out.append(ch)
    return "".join(out)


def unescape(text: str) -> str:
    """The inverse of :func:`escape`: ``\\n`` and ``\\t`` by name, ``\\x`` as ``x``."""
    return re.sub(r"\\(.)", lambda m: _UNNAMED.get(m.group(1), m.group(1)), text)
