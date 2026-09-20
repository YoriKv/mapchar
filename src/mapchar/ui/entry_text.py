"""What a row of the Files panel says: its label, its status mark, its notices
and its tooltip, and the order a group of rows is sorted into.

No Qt: the panel (:mod:`mapchar.ui.files_panel`) puts what is built here on
its tree items.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from mapchar.core.block import (
    NestedPointerSource,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    Status,
    source_start,
)
from mapchar.core.notices import notice_lines
from mapchar.core.text import fold
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.kind_names import SOURCE_NAMES
from mapchar.ui.token_text import ellipsize

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Iterable

    Counts = tuple[int, int, int, int]
    """A block's strings, and how many of them are edited, in review and done."""

ROW_PREVIEW_CHARS = 48
"""How much of a string a Files row shows before cutting it short."""


def string_preview(text: str) -> str:
    """One line of a string's text, cut short with an ellipsis.

    Line breaks and runs of spaces fold to one space — a row is a line — and
    a string that shows nothing says so, rather than being a bare number.
    """
    flat = " ".join(text.split())
    if not flat:
        return "(empty)"
    return ellipsize(flat, ROW_PREVIEW_CHARS)


def entry_offset(
    entry: Entry, rows_of: Callable[[Entry], list[Entry]] | None = None
) -> int:
    """Where a row sits in its parent file — what an Offset sort orders by.

    A compressed block is addressed by its compressed slot; every other block
    by where its source begins, which for a pointer list is its first pointer.
    A folder is where the earliest row inside it is, asked of ``rows_of``. A
    row that is not inside a file, and an empty folder, sorts first.
    """
    if entry.kind is EntryKind.FOLDER:
        inside = [entry_offset(e, rows_of) for e in (rows_of(entry) if rows_of else ())]
        inside = [at for at in inside if at >= 0]
        return min(inside) if inside else -1
    if entry.kind is EntryKind.BOOKMARK:
        return entry.bookmark_offset
    if entry.kind is not EntryKind.BLOCK:
        return -1
    if entry.compression_id:
        return entry.slot_offset
    start = source_start(entry.config.source if entry.config is not None else None)
    return 0 if start is None else start


def sorted_entries(
    entries: list[Entry],
    key: str,
    rows_of: Callable[[Entry], list[Entry]] | None = None,
) -> list[Entry]:
    """``entries`` in ``key`` order — the group a Sort by acts on.

    By name and by type, folders come first, as a file manager lists them; by
    offset a folder sits where the earliest row inside it does (``rows_of``
    gives a folder's rows).
    """
    if key == "Name":
        return sorted(
            entries, key=lambda e: (e.kind is not EntryKind.FOLDER, fold(e.name))
        )
    if key == "Type":
        return sorted(
            entries,
            key=lambda e: (e.kind is not EntryKind.FOLDER, e.kind.value, fold(e.name)),
        )
    return sorted(entries, key=lambda e: entry_offset(e, rows_of))


def label(entry: Entry, extra: str | None = None) -> str:
    """The row's text: the name, what the row holds, and ``●`` when unsaved.

    ``extra`` stands in for what the row holds when the caller has worked it
    out already — a folder's summary is its rows', which only the panel knows.
    """
    mark = " ●" if entry.dirty else ""
    if extra is not None:
        return f"{entry.name}{extra}{mark}"
    extra = ""
    if entry.kind is EntryKind.BLOCK:
        extra = block_extra(entry)
    if entry.kind is EntryKind.TABLE and entry.table is not None:
        extra = f"  ({len(entry.table.entries)})"
    if entry.kind is EntryKind.FILE:
        bits = []
        if entry.extra_paths:
            bits.append(f"{1 + len(entry.extra_paths)} files")
        if entry.container_id != "raw":
            bits.append(entry.container_id)
        if bits:
            extra = f"  [{' · '.join(bits)}]"
    return f"{entry.name}{extra}{mark}"


def status_counts(entry: Entry) -> Counts | None:
    """A block's string count, and how many are edited, in review and done;
    ``None`` when it has no strings to count.

    Counted off the records when the block is loaded, else off the state it
    is carrying with no document to hold it, so a block the session has not
    opened still says how much work is in it.
    """
    if entry.doc is not None:
        statuses = Counter(rec.status for rec in entry.doc.strings)
    elif entry.pending_strings:
        statuses = Counter(st.status for st in entry.pending_strings.values())
    else:
        return None
    return (
        statuses.total(),
        statuses[Status.EDITED],
        statuses[Status.REVIEW],
        statuses[Status.DONE],
    )


def _summary(first: str, counts: Counts) -> str:
    parts = [first]
    for word, n in zip(("edited", "review", "done"), counts[1:], strict=True):
        if n:
            parts.append(f"{n} {word}")
    return f"  ({', '.join(parts)})"


def block_extra(entry: Entry, counts: Counts | None = None) -> str:
    """A block's string count and status summary (:func:`status_counts`,
    unless ``counts`` already holds them)."""
    if counts is None:
        counts = status_counts(entry)
    return "" if counts is None else _summary(str(counts[0]), counts)


def folder_extra(items: int, counts: Iterable[Counts]) -> str:
    """A folder's summary: how many rows it holds directly, and the statuses of
    every block inside it at any depth, added up."""
    strings = edited = review = done = 0
    for c in counts:
        strings += c[0]
        edited += c[1]
        review += c[2]
        done += c[3]
    words = f"{items} item{'' if items == 1 else 's'}"
    return _summary(words, (strings, edited, review, done))


def status_mark(entry: Entry, tables: Container[str]) -> tuple[str, str]:
    """``('?' | '!' | '', why)`` — the status column and what it stands for.

    Missing wins over a notice: a file that is not there cannot have been
    read, so any notice on the entry is from an older load.
    """
    if entry.missing:
        return "?", "the file is not where the project says it is"
    serious, rest = notices(entry, tables)
    lines = serious + rest
    return ("!" if serious else "", "\n".join(lines)) if lines else ("", "")


def notices(entry: Entry, tables: Container[str]) -> tuple[list[str], list[str]]:
    """What a read had to give up on this row, as (warnings, the rest).

    Only a warning raises the ``!``; an info notice still earns its line in
    the tooltip, which is where a user looks to ask what is wrong with a row.
    """
    serious: list[str] = []
    rest: list[str] = []
    if entry.kind is EntryKind.BLOCK and entry.config is not None:
        table_id = entry.config.table_id
        if table_id and table_id not in tables:
            serious.append(
                f"start table @{table_id} is not loaded: the strings here are "
                "kept but cannot be re-read"
            )
    if entry.doc is not None:
        serious += [f"no write-back: {name}" for name in entry.doc.missing_plugins]
        # Both halves: what the byte stages had to assume while loading, and
        # what the extraction found in the strings.
        for notice in list(entry.doc.ctx.notices) + list(entry.doc.notices):
            (serious if notice.is_warning else rest).extend(notice_lines(notice))
    return serious, rest


def tooltip(entry: Entry, why: str = "") -> str:
    if entry.kind is EntryKind.FOLDER:
        # Its file's path would say nothing the row above it does not.
        return "folder: groups rows in the Files panel\ndouble-click or F2 to rename"
    lines = [entry.path or "(in memory)"]
    if entry.kind is EntryKind.FILE:
        for n, path in enumerate(entry.extra_paths, 2):
            lines.append(f"{n}. {path}")
        lines.append(f"container: {entry.container_id}")
    if entry.kind is EntryKind.BLOCK and entry.config is not None:
        lines.append(f"source: {source_text(entry)}")
        lines.append(f"start table: @{entry.config.table_id or '-'}")
        if entry.compression_id:
            lines.append(f"compression: {entry.compression_id}")
    if entry.kind is EntryKind.BOOKMARK:
        lines.append(f"offset: {entry.bookmark_offset:X}")
        lines.append("double-click to jump")
    if entry.kind is EntryKind.TABLE:
        lines.append(f"dialect: {entry.dialect or 'native'}")
        if entry.table is not None:
            lines.append(f"table: @{entry.table.id}")
    if entry.dirty:
        lines.append("unsaved edits")
    if why:
        lines.append(why)
    return "\n".join(lines)


def group_label(table: int | None, base: int | None, strings: int) -> str:
    """What a nested block's group row says: the inner pointer table that
    reached its strings, and how many they are.

    A group whose record is no longer among the source's — the outer table
    moved under a reading that has not caught up — has no table address to
    show, so it says the base its pointers count from instead.
    """
    if table is not None:
        where = f"{table:X}"
    elif base is not None:
        where = f"base {base:X}"
    else:
        where = "?"
    return f"{where}  {strings} string{'' if strings == 1 else 's'}"


def group_tooltip(table: int | None, base: int | None, strings: int) -> str:
    """The group row's hover: both addresses the group is bounded by."""
    lines = []
    if table is not None:
        lines.append(f"inner pointer table at {table:X}")
    if base is not None:
        lines.append(f"its pointers count from {base:X}")
    lines.append(f"{strings} string{'' if strings == 1 else 's'}")
    return "\n".join(lines)


def source_text(entry: Entry) -> str:
    """A block's source as offset and length, in the parent's coordinates.

    A row's own register is lower case, but the words are the Reading bar's
    (:mod:`mapchar.ui.kind_names`) — never a class name, which ``docs/ui.md``
    forbids outright.
    """
    if entry.compression_id:
        length = f"{entry.slot_length:X}" if entry.slot_length else "found on read"
        return f"compressed at {entry.slot_offset:X}, length {length}"
    source = entry.config.source
    named = SOURCE_NAMES.get(type(source), "source").lower()
    if isinstance(source, RangeSource):
        return f"{source.start:X}–{source.stop:X}"
    if isinstance(source, (PointerTableSource, NestedPointerSource)):
        return f"{named} {source.start:X}–{source.stop:X}"
    if isinstance(source, PointerListSource):
        return f"{len(source.addresses)} pointers"
    return named


__all__ = [
    "block_extra",
    "entry_offset",
    "folder_extra",
    "group_label",
    "group_tooltip",
    "label",
    "notices",
    "sorted_entries",
    "source_text",
    "status_counts",
    "status_mark",
    "string_preview",
    "tooltip",
]
