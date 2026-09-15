"""The Atlas 1.11 table dialect: ``docs/table-dialects.md``."""

from __future__ import annotations

from mapchar.core.errors import TableError
from mapchar.core.notices import Level, Notice
from mapchar.core.table import Entry, Table, TokenKind
from mapchar.project.formats.legacy import (
    _HEXKEY,
    _add_or_note,
    _even_key,
    legacy_text,
)
from mapchar.project.formats.table_native import TableFile
from mapchar.project.formats.textfile import split_lines


def read_atlas(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    table = Table(default_id)
    notices: list[Notice] = []
    for n, line in enumerate(split_lines(text), start=1):
        if not line.strip() or line.startswith("//"):
            continue
        first = line[0]
        if first in "([{":
            continue
        if first == "$":
            notices.append(
                Notice(f"line {n}: '$' entry skipped, as Atlas does", Level.INFO)
            )
            continue
        if first in "!@":
            notices.append(Notice(f"line {n}: dakuten entry dropped", Level.INFO))
            continue
        if first == "*":
            key, eq, value = line[1:].partition("=")
            bits = _even_key(key, path, n)
            _add_or_note(
                table,
                Entry(bits, TokenKind.TEXT, legacy_text(value + "\n")),
                n,
                notices,
            )
            continue
        if first == "/":
            key, eq, value = line[1:].partition("=")
            if not eq:
                notices.append(
                    Notice(
                        f"line {n}: hexless end marker {key!r} dropped: it names no"
                        " byte to read",
                        Level.INFO,
                    )
                )
                continue
            bits = _even_key(key, path, n)
            _add_or_note(
                table, Entry(bits, TokenKind.END, legacy_text(value)), n, notices
            )
            continue
        key, eq, value = line.partition("=")
        if not eq or not _HEXKEY.match(key):
            raise TableError("not a table entry", path, n)
        bits = _even_key(key, path, n)
        _add_or_note(table, Entry(bits, TokenKind.TEXT, legacy_text(value)), n, notices)
    return TableFile(table, notices, "atlas")
