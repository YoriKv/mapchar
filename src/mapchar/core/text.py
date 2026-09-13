"""Text handling the file formats share: line splitting and backslash escapes."""

from __future__ import annotations

import re

_NAMED = {"\t": "\\t"}
"""Characters with a named escape, when they are escaped at all."""

_UNNAMED = {"n": "\n", "t": "\t"}


def split_lines(text: str) -> list[str]:
    """The lines of a text file: a byte-order mark off, any line ending."""
    if text.startswith("﻿"):
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
