"""Qt-free helpers shared by test modules."""

from __future__ import annotations

from mapchar.core.table import Table, TableSet
from mapchar.project.formats.table_native import HEADER, parse_native


def tables_from(body: str) -> dict[str, Table]:
    """Parse native table text (header added) into ``{id: Table}``."""
    return {t.id: t for t in parse_native(HEADER + "\n" + body).tables}


def table_set(body: str, start: str | None = None) -> TableSet:
    tables = tables_from(body)
    first = start or next(iter(tables))
    return TableSet.build(tables[first], tables)
