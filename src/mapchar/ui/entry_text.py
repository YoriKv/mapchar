"""What a row of the Files panel says: its label, its status mark, its notices
and its tooltip, and the order a group of rows is sorted into.

No Qt: the panel (:mod:`mapchar.ui.files_panel`) puts what is built here on
its tree items.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mapchar.core.block import (
    PointerListSource,
    PointerTableSource,
    RangeSource,
    Status,
    source_start,
)
from mapchar.core.notices import notice_lines
from mapchar.core.text import fold
from mapchar.project.workspace import Entry, EntryKind

if TYPE_CHECKING:
    from collections.abc import Container

PREVIEW_CHARS = 48
"""How much of a string a row shows before cutting it short."""


def string_preview(text: str) -> str:
    """One line of a string's text, cut short with an ellipsis.

    Line breaks and runs of spaces fold to one space — a row is a line — and
    a string that shows nothing says so, rather than being a bare number.
    """
    flat = " ".join(text.split())
    if not flat:
        return "(empty)"
    if len(flat) <= PREVIEW_CHARS:
        return flat
    return flat[: PREVIEW_CHARS - 1] + "…"


def entry_offset(entry: Entry) -> int:
    """Where a row sits in its parent file — what an Offset sort orders by.

    A compressed block is addressed by its compressed slot; every other block
    by where its source begins, which for a pointer list is its first pointer.
    A row that is not inside a file sorts first.
    """
    if entry.kind is EntryKind.BOOKMARK:
        return entry.bookmark_offset
    if entry.kind is not EntryKind.BLOCK:
        return -1
    if entry.compression_id:
        return entry.slice_offset
    start = source_start(entry.config.source if entry.config is not None else None)
    return 0 if start is None else start


def sorted_entries(entries: list[Entry], key: str) -> list[Entry]:
    """``entries`` in ``key`` order — the group a Sort by acts on."""
    if key == "Name":
        return sorted(entries, key=lambda e: fold(e.name))
    if key == "Type":
        return sorted(entries, key=lambda e: (e.kind.value, fold(e.name)))
    return sorted(entries, key=entry_offset)


def label(entry: Entry) -> str:
    mark = " ●" if entry.dirty else ""
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


def block_extra(entry: Entry) -> str:
    """A block's string count and status summary.

    Counted off the records when the block is loaded, else off the
    translations it is carrying with no document to hold them, so a block
    the session has not opened still says how much work is in it.
    """
    if entry.doc is not None:
        statuses = [rec.status for rec in entry.doc.strings]
        total = len(statuses)
        too_long = entry.doc.too_long
    elif entry.pending_strings:
        statuses = [st.status for st in entry.pending_strings.values()]
        total = len(statuses)
        too_long = 0
    else:
        return ""
    parts = [str(total)]
    for label, n in (
        ("edited", sum(s is Status.EDITED for s in statuses)),
        ("too long", too_long),
        ("review", sum(s is Status.REVIEW for s in statuses)),
    ):
        if n:
            parts.append(f"{n} {label}")
    return f"  ({', '.join(parts)})"


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


def source_text(entry: Entry) -> str:
    """A block's source as offset and length, in the parent's coordinates."""
    if entry.compression_id:
        length = f"{entry.slice_length:X}" if entry.slice_length else "found on read"
        return f"compressed at {entry.slice_offset:X}, length {length}"
    source = entry.config.source
    if isinstance(source, RangeSource):
        return f"{source.start:X}–{source.stop:X}"
    if isinstance(source, PointerTableSource):
        return f"pointer table {source.start:X}–{source.stop:X}"
    if isinstance(source, PointerListSource):
        return f"{len(source.addresses)} pointers"
    return source.__class__.__name__


__all__ = [
    "PREVIEW_CHARS",
    "block_extra",
    "entry_offset",
    "label",
    "notices",
    "sorted_entries",
    "source_text",
    "status_mark",
    "string_preview",
    "tooltip",
]
