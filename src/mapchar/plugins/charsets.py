"""Applying a table's ``@charset`` from the registry."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping

from mapchar.core.errors import TableError
from mapchar.core.notices import Level, Notice
from mapchar.core.table import Table, TableEntry, TokenKind
from mapchar.core.tokens import escape_text
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import Registry


def _charset_aliases(
    charset, notices: list[Notice] | None
) -> Iterable[tuple[str, str]]:
    """``aliases()`` if the charset has one and it answers, else nothing.

    The probe :func:`mapchar.pipeline.pipeline._probe` runs over a container's
    optional hooks, in the one place a charset's are reached: absence is already
    defined as no aliases, so a charset that *cannot* answer is read as one that
    never offered any — a display detail must not take a table load down — and
    the notice is how its author finds out.
    """
    ask = getattr(charset, "aliases", None)
    if not callable(ask):
        return ()
    try:
        return list(ask())
    except Exception as exc:  # noqa: BLE001 - a probe must not fail the load
        if notices is not None:
            notices.append(
                Notice(
                    f"charset {charset.info.id} could not answer aliases(),"
                    " so it was read as offering none",
                    Level.WARNING,
                    detail=f"{exc}\nRead as if the charset had not defined"
                    " aliases(), which is what one staying quiet means.",
                    source=charset.info.id,
                )
            )
        return ()


def apply_charset(
    table: Table, registry: Registry, notices: list[Notice] | None = None
) -> None:
    """Fill ``table`` with its charset's codes under the file's own entries.

    File entries win code for code; a file entry with empty text removes the
    charset's code, and is kept where the charset has none, to remove what an
    included table gives. Idempotent: a table already filled is left alone.

    A charset may also offer ``aliases()``: text the encoder accepts for a
    code whose own text is something else, which is how the yen sign reaches
    Shift-JIS ``5C`` while ``5C`` still decodes as a backslash. It is optional
    and probed — one that raises is read as one that offered none, recorded on
    ``notices`` when the caller keeps a list.
    """
    if table.charset == "none" or table.charset_applied:
        return
    charset = registry.plugin(Stage.CHARSET, table.charset)
    if charset is None:
        raise TableError(f"unknown charset {table.charset!r} in table {table.id!r}")
    own = dict(table.entries)
    table.charset_entries = {}
    for bits, text in charset.entries():
        entry = TableEntry(bits, TokenKind.TEXT, escape_text(text))
        table.charset_entries[bits] = entry
        if bits in table.entries:
            continue
        table.add(entry)
    charset_keys = table.charset_entries
    for text, bits in _charset_aliases(charset, notices):
        table.add_alias(escape_text(text), bits)
    for bits, entry in own.items():
        # Empty text removes a charset code; anywhere else it is an entry of
        # its own — one that removes a key an included table gives.
        if entry.kind is TokenKind.TEXT and entry.text == "" and bits in charset_keys:
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
    table.add(TableEntry(end_bits(charset), TokenKind.END, "[end]"), replace=True)
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
