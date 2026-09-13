"""A table file on disk, the tables an entry holds, and the edits over them.

Reading is one half: :func:`read_table_file` turns a file in any dialect into
tables. The other half is the **overlay** — the entries added, changed and
removed in the app, which the project file carries rather than rewriting a table
file other tools read. The two meet on the entry: what the file gave is the
baseline (:attr:`~mapchar.project.workspace.Entry.file_tables`), the overlay is
laid back over it on every read, and **Save Table** folds it in.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from copy import deepcopy
from typing import TYPE_CHECKING

from mapchar.core.errors import MapcharError
from mapchar.core.notices import Level, Notice
from mapchar.core.table import Table
from mapchar.core.text import read_text_any
from mapchar.plugins.charsets import apply_charset
from mapchar.project.formats.table_legacy import load_table_text
from mapchar.project.formats.table_native import format_entry, parse_entry

if TYPE_CHECKING:
    from mapchar.plugins.registry import Registry
    from mapchar.project.formats.table_native import TableFile
    from mapchar.project.workspace import Entry as WorkspaceEntry


def read_table_file(
    path: str, dialect: str | None = None, registry: Registry | None = None
) -> TableFile:
    """The tables in ``path``, read in ``dialect``; ``None`` detects it.

    The encoding is decided by :func:`~mapchar.core.text.read_text_any`: UTF-8,
    else ``cp932`` for the Shift-JIS tables legacy tools left behind, else
    ``latin-1``. Anything but UTF-8 is recorded as a notice and left on
    ``TableFile.encoding``, so the UI can say what the file was read as. Given a
    ``registry``, every table has its charset applied. Raises ``OSError`` for
    the file and :class:`~mapchar.core.errors.MapcharError` for its contents.
    """
    text, encoding = read_text_any(path)
    tf = load_table_text(text, path, dialect)
    tf.encoding = encoding
    if encoding not in ("utf-8", "utf-8-sig"):
        tf.notices.insert(
            0,
            Notice(
                f"{os.path.basename(path)} is not UTF-8; read as {encoding}",
                Level.INFO,
                detail="Its bytes do not decode as UTF-8. Save As Native writes "
                "the table back as UTF-8.",
                source="tables",
            ),
        )
    if registry is not None:
        for table in tf.tables:
            apply_charset(table, registry)
    return tf


def overlay_of(
    base: list[Table], live: list[Table]
) -> dict[str, dict[str, str | None]]:
    """How ``live`` differs from ``base``, per table id: the project's overlay.

    One entry added or changed is its line in the native grammar, one removed is
    ``None``, and a table ``base`` has not got at all contributes every entry it
    holds — which is how a table with no file behind it is carried whole. Tables
    ids are compared, never object identity, so a re-read file measures against
    the same baseline.
    """
    was = {t.id: t for t in base}
    overlay: dict[str, dict[str, str | None]] = {}
    for table in live:
        before = was.get(table.id)
        rows: dict[str, str | None] = {}
        for bits, entry in table.entries.items():
            old = before.entries.get(bits) if before is not None else None
            if old != entry:
                rows[bits] = format_entry(entry)
        if before is not None:
            for bits in before.entries:
                if bits not in table.entries:
                    rows[bits] = None
        if rows:
            overlay[table.id] = rows
    return overlay


def apply_overlay(
    tables: list[Table], overlay: dict[str, dict[str, str | None]]
) -> None:
    """Lay ``overlay`` over ``tables`` in place, the reverse of :func:`overlay_of`.

    A table id the overlay names and ``tables`` has not got is created, so a
    table the project carries whole arrives even with no file to read. A line
    that no longer parses is skipped rather than fatal: the rest of the project
    still opens.
    """
    by_id = {t.id: t for t in tables}
    for table_id, rows in overlay.items():
        table = by_id.get(table_id)
        if table is None:
            try:
                table = Table(table_id)
            except MapcharError:
                continue
            by_id[table_id] = table
            tables.append(table)
        for bits, line in rows.items():
            if line is None:
                table.remove(bits)
                continue
            try:
                table.add(parse_entry(line), replace=True)
            except (ValueError, MapcharError):
                continue


def adopt_tables(
    entry: WorkspaceEntry, tables: list[Table], notices: Sequence[Notice] = ()
) -> None:
    """Give ``entry`` the tables a file just yielded, overlay laid back on top.

    The one way a read reaches an entry: what the file gave becomes the baseline
    the overlay is measured against, and the in-app edits the project is holding
    go straight back over it — so a reload from disk picks up the file's changes
    without discarding the user's. ``notices`` is what that read had to say, kept
    on the entry for the Table Editor to show, and replaced with every re-read.
    """
    entry.file_tables = deepcopy(tables)
    entry.tables = tables
    entry.notices = tuple(notices)
    apply_overlay(entry.tables, entry.table_overlay)


def capture_overlay(entry: WorkspaceEntry) -> None:
    """Re-measure ``entry``'s overlay after its tables changed.

    Derived rather than accumulated, so an undo, a redo and a reload all leave
    the project holding exactly what the tables now say.
    """
    entry.table_overlay = overlay_of(entry.file_tables, entry.tables)


def fold_overlay(entry: WorkspaceEntry) -> None:
    """The tables are now what the file holds: the overlay is spent.

    What **Save Table** leaves behind — the native file it wrote is the
    baseline, so there is nothing left over it.
    """
    entry.file_tables = deepcopy(entry.tables)
    entry.table_overlay = {}
