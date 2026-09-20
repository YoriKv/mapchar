"""The ``@include`` layering: a table read over the tables it starts from.

An include is resolved by walking the graph of tables a table reaches, laying
each one's entries under the one that includes it. The answer is a table of
its own, kept on the table it was resolved from until any table it reaches
changes (:meth:`~mapchar.core.table.Table.cached`).
"""

from __future__ import annotations

from collections.abc import Mapping

from mapchar.core.errors import TableError
from mapchar.core.table import Table, TableEntry, TokenKind


def inherited(
    table: Table, available: Mapping[str, Table]
) -> dict[str, tuple[TableEntry, str]]:
    """What ``table``'s includes give it before its own entries apply: per key,
    the entry and the id of the table whose own entry it is.

    Raises :class:`~mapchar.core.errors.TableError` for an include no table in
    ``available`` answers to and for tables that include each other; a label
    the merged table holds twice is :func:`resolve`'s to report.
    """
    layers: dict[str, tuple[TableEntry, str]] = {}
    for inc in table.includes:
        layers.update(_layers(_included(table, inc, available), available, (table.id,)))
    return layers


def _included(table: Table, inc: str, available: Mapping[str, Table]) -> Table:
    target = available.get(inc)
    if target is None:
        raise TableError(f"table {table.id!r} includes unknown table {inc!r}")
    return target


def _layers(
    table: Table, available: Mapping[str, Table], visiting: tuple[str, ...]
) -> dict[str, tuple[TableEntry, str]]:
    """Every entry resolved ``table`` holds, with the table it is own to."""
    if table.id in visiting:
        cycle = " → ".join((*visiting[visiting.index(table.id) :], table.id))
        raise TableError(f"tables include each other: {cycle}")
    below: dict[str, tuple[TableEntry, str]] = {
        bits: (e, table.id) for bits, e in table.charset_entries.items()
    }
    for inc in table.includes:
        target = _included(table, inc, available)
        below.update(_layers(target, available, (*visiting, table.id)))
    return _lay_own(table, below)


def _lay_own(
    table: Table, below: dict[str, tuple[TableEntry, str]]
) -> dict[str, tuple[TableEntry, str]]:
    """``table``'s own entries over ``below``: an entry with empty text over a
    key ``below`` gives removes that key, as it does a charset's code."""
    merged = dict(below)
    for e in table.own_entries():
        if e.kind is TokenKind.TEXT and e.text == "" and e.bits in below:
            del merged[e.bits]
        else:
            merged[e.bits] = (e, table.id)
    return merged


def resolve(table: Table, available: Mapping[str, Table]) -> Table:
    """``table`` with everything it includes laid under its own entries.

    A table that includes nothing is itself. Otherwise the answer is a table of
    its own, remembered on ``table`` until it or anything it includes changes.
    Raises :class:`~mapchar.core.errors.TableError` as :func:`inherited` does.
    """
    if not table.includes:
        return table

    def make() -> Table:
        merged = _layers(table, available, ())
        out = Table(table.id, table.charset)
        out.comment = table.comment
        out.charset_applied = table.charset_applied
        out.charset_entries = dict(table.charset_entries)
        out.includes = table.includes
        seen: dict[str, tuple[str, str]] = {}
        for bits, (entry, origin) in merged.items():
            label = entry.label
            if label is None:
                continue
            other = seen.get(label)
            if other is not None:
                where = sorted({origin, other[1]})
                raise TableError(
                    f"duplicate label [{label}] in table {table.id!r}"
                    + (f" (from {' and '.join(where)})" if where != [table.id] else "")
                )
            seen[label] = (bits, origin)
        for entry, _ in merged.values():
            out.add(entry, replace=True)
        for inc in table.includes:
            for text, bits in resolve(available[inc], available).aliases.items():
                if bits in out.entries:
                    out.aliases[text] = bits
        for text, bits in table.aliases.items():
            if bits in out.entries:
                out.aliases[text] = bits
        return out

    return table.cached("resolved", make, key=_resolve_key(table, available, ()))


def _resolve_key(
    table: Table, available: Mapping[str, Table], visiting: tuple[str, ...]
) -> tuple:
    """What a resolution of ``table`` depends on: every table it reaches by
    ``@include``, as the object and the revision it was at."""
    if table.id in visiting:
        cycle = " → ".join((*visiting[visiting.index(table.id) :], table.id))
        raise TableError(f"tables include each other: {cycle}")
    parts: list = [id(table), table.revision]
    for inc in table.includes:
        target = _included(table, inc, available)
        parts.append(_resolve_key(target, available, (*visiting, table.id)))
    return tuple(parts)
