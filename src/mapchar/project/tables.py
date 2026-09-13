"""Reading a table file from disk into the tables an entry holds."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mapchar.plugins.charsets import apply_charset
from mapchar.project.formats.table_legacy import load_table_text

if TYPE_CHECKING:
    from mapchar.plugins.registry import Registry
    from mapchar.project.formats.table_native import TableFile


def read_table_file(
    path: str, dialect: str | None = None, registry: Registry | None = None
) -> TableFile:
    """The tables in ``path``, read in ``dialect``; ``None`` detects it.

    Undecodable bytes become replacement characters, so a table file in an
    unknown encoding still loads with notices rather than failing. Given a
    ``registry``, every table has its charset applied. Raises ``OSError`` for
    the file and :class:`~mapchar.core.errors.MapcharError` for its contents.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    tf = load_table_text(text, path, dialect)
    if registry is not None:
        for table in tf.tables:
            apply_charset(table, registry)
    return tf
