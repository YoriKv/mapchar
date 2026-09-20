"""How a text file is spelled: encoding, line endings and backslash escapes.

The one place in mapchar that opens a text file. The model itself
(:mod:`mapchar.core.text`) knows nothing of any of this.
"""

from __future__ import annotations

import os
import re

from mapchar.core.text import nfc

_NAMED = {"\t": "\\t"}
"""Characters with a named escape, when they are escaped at all."""

_UNNAMED = {"n": "\n", "t": "\t"}

BOM = "﻿"

_ENCODINGS = ("utf-8-sig", "cp932", "latin-1")
"""Tried in order by :func:`read_text_any`; the last one always decodes."""


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


def not_utf8(path: str, encoding: str) -> str | None:
    """What a read that was not UTF-8 has to say, or ``None`` for one that was.

    The one wording every reader's notice uses, so a table, a script and a
    command file report the same thing the same way.
    """
    if encoding in ("utf-8", "utf-8-sig"):
        return None
    return f"{os.path.basename(path)} is not UTF-8; read as {encoding}"


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


def quoted(text: str) -> str:
    """``text`` in double quotes, spelled as :func:`escape` spells it: how a
    script directive and a PO field name a string."""
    return '"' + escape(text, '"') + '"'


def unescape(text: str) -> str:
    """The inverse of :func:`escape`: ``\\n`` and ``\\t`` by name, ``\\x`` as ``x``."""
    return re.sub(r"\\(.)", lambda m: _UNNAMED.get(m.group(1), m.group(1)), text)
