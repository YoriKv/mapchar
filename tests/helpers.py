"""Qt-free helpers shared by test modules."""

from __future__ import annotations

from mapchar.core.table import Table, TableSet
from mapchar.project.formats.table_native import HEADER, parse_native


def tables_from(body: str) -> dict[str, Table]:
    """Parse native table text (header added) into ``{id: Table}``."""
    from mapchar.plugins.charsets import apply_charset
    from mapchar.plugins.registry import default_registry

    tables = parse_native(HEADER + "\n" + body).tables
    registry = default_registry()
    for t in tables:
        apply_charset(t, registry)
    return {t.id: t for t in tables}


def table_set(body: str, start: str | None = None) -> TableSet:
    tables = tables_from(body)
    first = start or next(iter(tables))
    return TableSet.build(tables[first], tables)
