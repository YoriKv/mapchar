"""A table file on disk, the table an entry holds, and the edits over it.

Reading is one half: :func:`read_table_file` turns a file in any dialect into
a table. The other half is the **overlay** — the entries added, changed and
removed in the app, which the project file carries rather than rewriting a table
file other tools read. The two meet on the entry: what the file gave is the
baseline (:attr:`~mapchar.project.workspace.Entry.file_table`), the overlay is
laid back over it on every read, and **Save As File** folds it in.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from copy import deepcopy
from typing import TYPE_CHECKING

from mapchar.core.errors import MapcharError
from mapchar.core.notices import Level, Notice
from mapchar.core.table import Table
from mapchar.plugins.charsets import apply_charset
from mapchar.project.formats.legacy import load_table_text
from mapchar.project.formats.table_native import (
    format_entry_lines,
    parse_entry_lines,
    sanitize_id,
)
from mapchar.project.formats.textfile import read_text_any
from mapchar.project.workspace import free_name

if TYPE_CHECKING:
    from mapchar.plugins.registry import Registry
    from mapchar.project.formats.table_native import TableFile
    from mapchar.project.workspace import Entry as WorkspaceEntry


def table_id_for(path: str | None) -> str:
    """The name a table file without a ``@table`` line gives its table."""
    if not path:
        return "table"
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return sanitize_id(name) or "table"


def free_table_id(table_id: str, taken) -> str:
    """``table_id``, numbered up (``main_2``) until it is not in ``taken``."""
    return free_name(table_id, taken, "{name}_{n}")


def read_table_file(
    path: str, dialect: str | None = None, registry: Registry | None = None
) -> TableFile:
    """The table in ``path``, read in ``dialect``; ``None`` detects it.

    The encoding is decided by
    :func:`~mapchar.project.formats.textfile.read_text_any`: UTF-8, else
    ``cp932`` for the Shift-JIS tables legacy tools left behind, else
    ``latin-1``. Anything but UTF-8 is left on ``TableFile.encoding`` and said
    in a notice, which is what the Table Editor shows. Given a ``registry``,
    every table has its charset applied. Raises ``OSError`` for the file and
    :class:`~mapchar.core.errors.MapcharError` for its contents.
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
                detail="Its bytes do not decode as UTF-8. Save As File writes "
                "the table back as UTF-8.",
                source="tables",
            ),
        )
    if registry is not None:
        for table in tf.tables:
            apply_charset(table, registry, tf.notices)
    return tf


def same_table(a: Table | None, b: Table | None) -> bool:
    """Whether two tables say the same thing.

    What a reader of the table can tell apart — its id, charset, comment,
    includes and entries — not the caches derived from them. How a re-read
    decides whether anything reached the UI.
    """
    if a is None or b is None:
        return a is b
    return (
        a.id == b.id
        and a.charset == b.charset
        and a.comment == b.comment
        and a.includes == b.includes
        and a.entries == b.entries
    )


def overlay_of(base: Table | None, live: Table) -> dict[str, str | None]:
    """How ``live`` differs from ``base``: the project's overlay.

    Per entry key, one entry added or changed is its lines in the native
    grammar — its comment lines, then its own — and one removed is ``None``.
    With no ``base`` every entry is an addition, which is how a table with no
    file behind it is carried whole.
    """
    rows: dict[str, str | None] = {}
    for bits, entry in live.entries.items():
        old = base.entries.get(bits) if base is not None else None
        if old != entry:
            rows[bits] = "\n".join(format_entry_lines(entry))
    if base is not None:
        for bits in base.entries:
            if bits not in live.entries:
                rows[bits] = None
    return rows


def apply_overlay(table: Table, overlay: dict[str, str | None]) -> None:
    """Lay ``overlay`` over ``table`` in place, the reverse of :func:`overlay_of`.

    A line that no longer parses is skipped rather than fatal: the rest of the
    project still opens.
    """
    for bits, line in overlay.items():
        if line is None:
            table.remove(bits)
            continue
        try:
            table.add(parse_entry_lines(line), replace=True)
        except (ValueError, MapcharError):
            continue


def adopt_table(
    entry: WorkspaceEntry,
    table: Table,
    notices: Sequence[Notice] = (),
    *,
    from_file: bool = True,
    registry: Registry | None = None,
) -> None:
    """Give ``entry`` the table a read just yielded, overlay laid back on top.

    The one way a read reaches an entry: what the file gave becomes the baseline
    the overlay is measured against, and the in-app edits the project is holding
    go straight back over it — so a reload from disk picks up the file's changes
    without discarding the user's. Not ``from_file``, ``table`` is an empty table
    named for the entry, and the overlay is all of it. ``notices`` is what the
    read had to say, kept on the entry for the Table Editor to show, and replaced
    with every re-read. Given a ``registry``, a charset the project puts on the
    table (:attr:`~mapchar.project.workspace.Entry.table_charset`) replaces the
    file's before the overlay goes on.
    """
    entry.file_table = deepcopy(table) if from_file else None
    entry.table = table
    entry.notices = tuple(notices)
    if registry is not None and entry.table_charset not in (None, table.charset):
        rebase_charset(entry, entry.table_charset, registry)
        table.replace_with(entry.file_table)
    if entry.table_id:
        table.id = entry.table_id
    if entry.table_includes is not None:
        table.includes = entry.table_includes
    apply_overlay(table, entry.table_overlay)


def baseline_for(
    entry: WorkspaceEntry, charset: str, registry: Registry
) -> tuple[Table, str]:
    """What ``entry``'s file says with ``charset`` in place of its own, and the
    file's own charset.

    Read again from the file rather than taken from the baseline held, which
    has a charset folded in that cannot be told apart from the file's entries;
    a table with no file starts from nothing.
    """
    if entry.path:
        text, _ = read_text_any(entry.path)
        table = load_table_text(text, entry.path, entry.dialect).table
    else:
        table = Table(entry.table.id if entry.table is not None else "table")
    own = table.charset
    table.charset = charset
    apply_charset(table, registry)
    return table, own


def rebase_charset(entry: WorkspaceEntry, charset: str, registry: Registry) -> None:
    """Measure ``entry``'s overlay against its file on ``charset``: the baseline
    is rebuilt, and :attr:`~mapchar.project.workspace.Entry.table_charset` says
    whether the project has to carry the charset. The live table is not
    touched: :func:`set_charset` and an undo each put their own contents in."""
    base, own = baseline_for(entry, charset, registry)
    entry.file_table = base
    entry.table_charset = None if charset == own else charset


def set_charset(entry: WorkspaceEntry, charset: str, registry: Registry) -> None:
    """Put ``entry``'s table on ``charset``, keeping its edits.

    What the table said beyond its old charset — its overlay — is what it says
    beyond the new one: the entries the old charset gave go, the new charset's
    come, and the file's own entries and the in-app edits stay on top.
    """
    if entry.table is None:
        return
    edits = overlay_of(entry.file_table, entry.table)
    table_id, includes = entry.table.id, entry.table.includes
    rebase_charset(entry, charset, registry)
    entry.table.replace_with(entry.file_table)
    entry.table.id = table_id  # the file's id is not the table's when renamed
    entry.table.includes = includes  # ...nor its includes when changed
    apply_overlay(entry.table, edits)
    capture_overlay(entry)


def capture_overlay(entry: WorkspaceEntry) -> None:
    """Re-measure ``entry``'s overlay after its table changed.

    Derived rather than accumulated, so an undo, a redo and a reload all leave
    the project holding exactly what the table now says.
    """
    if entry.table is not None:
        entry.table_overlay = overlay_of(entry.file_table, entry.table)
        base = entry.file_table
        entry.table_id = (
            entry.table.id if base is not None and entry.table.id != base.id else None
        )
        includes = entry.table.includes
        entry.table_includes = (
            includes
            if includes != (base.includes if base is not None else ())
            else None
        )


def fold_overlay(entry: WorkspaceEntry) -> None:
    """The table is now what the file holds: the overlay is spent.

    What **Save As File** leaves behind — the native file it wrote is the
    baseline, so there is nothing left over it.
    """
    entry.file_table = deepcopy(entry.table)
    entry.table_overlay = {}
    entry.table_includes = None
