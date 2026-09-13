"""Applying a table's ``@charset`` from the registry."""

from __future__ import annotations

from mapchar.core.errors import TableError
from mapchar.core.table import Entry, EntryKind, Table
from mapchar.core.tokens import escape_text
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import Registry


def apply_charset(table: Table, registry: Registry) -> None:
    """Fill ``table`` with its charset's codes under the file's own entries.

    File entries win code for code; a file entry with empty text removes the
    charset's code. Idempotent: a table already filled is left alone.
    """
    if table.charset == "none" or getattr(table, "_charset_applied", False):
        return
    charset = registry.plugin(Stage.CHARSET, table.charset)
    if charset is None:
        raise TableError(f"unknown charset {table.charset!r} in table {table.id!r}")
    own = dict(table.entries)
    for bits, text in charset.entries():
        if bits in table.entries:
            continue
        table.add(Entry(bits, EntryKind.TEXT, escape_text(text)))
    for bits, entry in own.items():
        if entry.kind is EntryKind.TEXT and entry.text == "":
            table.remove(bits)
    table._charset_applied = True  # type: ignore[attr-defined]
