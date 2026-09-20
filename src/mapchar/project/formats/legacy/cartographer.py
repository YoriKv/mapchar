"""The Cartographer PR3 table dialect: ``docs/table-dialects.md``."""

from __future__ import annotations

import re

from mapchar.core.errors import TableError
from mapchar.core.notices import Notice
from mapchar.core.table import OperandSpec, Table, TableEntry, TokenKind
from mapchar.project.formats.legacy import (
    _HEXKEY,
    _add_or_note,
    _even_key,
    legacy_text,
)
from mapchar.project.formats.table_native import TableFile, sanitize_label
from mapchar.project.formats.textfile import split_lines


def _cart_text(value: str) -> str:
    return legacy_text(value.replace("\\n", "\n").replace("\\r", "\n"))


def read_cartographer(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    table = Table(default_id)
    notices: list[Notice] = []
    for n, line in enumerate(split_lines(text), start=1):
        if not line.strip():
            continue
        first = line[0]
        if first == "$":
            m = re.match(r"^\$([0-9A-Fa-f]+)=(.*),(\d+)\s*$", line)
            if not m:
                raise TableError("bad linked entry", path, n)
            key, label, count = m.groups()
            bits = _even_key(key, path, n)
            entry = TableEntry(
                bits,
                TokenKind.CODE,
                sanitize_label(label),
                operands=(OperandSpec("bytes", int(count) * 8),),
            )
        elif first == "/":
            key, _, value = line[1:].partition("=")
            bits = _even_key(key, path, n)
            entry = TableEntry(bits, TokenKind.END, _cart_text(value))
        elif _HEXKEY.match(first):
            key, eq, value = line.partition("=")
            if not eq:
                raise TableError("entry without '='", path, n)
            bits = _even_key(key, path, n)
            entry = TableEntry(bits, TokenKind.TEXT, _cart_text(value))
        else:
            raise TableError("line must start with a hex digit, '/' or '$'", path, n)
        _add_or_note(table, entry, n, notices)
    return TableFile(table, notices, "cartographer")
