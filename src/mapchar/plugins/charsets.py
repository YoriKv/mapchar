"""Applying a table's ``@charset`` from the registry."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from mapchar.core.errors import TableError
from mapchar.core.table import Entry, Table, TokenKind
from mapchar.core.tokens import escape_text
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import Registry


def apply_charset(table: Table, registry: Registry) -> None:
    """Fill ``table`` with its charset's codes under the file's own entries.

    File entries win code for code; a file entry with empty text removes the
    charset's code. Idempotent: a table already filled is left alone.

    A charset may also offer ``aliases()``: text the encoder accepts for a
    code whose own text is something else, which is how the yen sign reaches
    Shift-JIS ``5C`` while ``5C`` still decodes as a backslash.
    """
    if table.charset == "none" or table.charset_applied:
        return
    charset = registry.plugin(Stage.CHARSET, table.charset)
    if charset is None:
        raise TableError(f"unknown charset {table.charset!r} in table {table.id!r}")
    own = dict(table.entries)
    table.charset_entries = {}
    for bits, text in charset.entries():
        entry = Entry(bits, TokenKind.TEXT, escape_text(text))
        table.charset_entries[bits] = entry
        if bits in table.entries:
            continue
        table.add(entry)
    for text, bits in getattr(charset, "aliases", lambda: ())():
        table.add_alias(escape_text(text), bits)
    for bits, entry in own.items():
        if entry.kind is TokenKind.TEXT and entry.text == "":
            table.remove(bits)
    table.charset_applied = True


def end_bits(charset) -> str:
    """The bits of a NUL in ``charset``: the terminator its strings end at.

    As wide as the codec's own code unit — two bytes in UTF-16, four in UTF-32 —
    and one byte for a charset that names no codec.
    """
    codec = getattr(charset, "codec", None)
    try:
        width = len("\0".encode(codec)) if codec else 1
    except (LookupError, UnicodeEncodeError):
        width = 1
    return "0" * 8 * max(width, 1)


def charset_table(charset_id: str, registry: Registry) -> Table:
    """A table that is ``charset_id`` and nothing more, ending strings at NUL.

    What the Table list offers under the loaded tables: a standard encoding
    picked without a table file, as C strings of it.
    """
    table = Table(charset_id, charset_id)
    apply_charset(table, registry)
    charset = registry.plugin(Stage.CHARSET, charset_id)
    table.add(Entry(end_bits(charset), TokenKind.END, "[end]"), replace=True)
    return table


class CharsetTables(Mapping[str, Table]):
    """Every registered charset as a table, keyed by the charset's id.

    Built on first use: a charset over the whole BMP is tens of thousands of
    entries, and listing the encodings must not cost reading them all.
    """

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self._built: dict[str, Table] = {}

    def names(self) -> list[tuple[str, str]]:
        """``(id, name)`` of every charset a table can be, in registry order."""
        return [
            (p.info.id, p.info.name)
            for p in self.registry.plugins(Stage.CHARSET)
            if p.info.id != "none"
        ]

    def __getitem__(self, charset_id: str) -> Table:
        table = self._built.get(charset_id)
        if table is None:
            if charset_id not in self:
                raise KeyError(charset_id)
            table = self._built[charset_id] = charset_table(charset_id, self.registry)
        return table

    def __contains__(self, charset_id: object) -> bool:
        return charset_id != "none" and charset_id in self.registry.ids(Stage.CHARSET)

    def __iter__(self) -> Iterator[str]:
        return iter(cid for cid, _ in self.names())

    def __len__(self) -> int:
        return len(self.names())
