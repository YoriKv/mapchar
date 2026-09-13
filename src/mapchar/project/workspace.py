"""The open-entries model: what is open, which is current, what is unsaved."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from itertools import count

from mapchar.core.block import BlockConfig
from mapchar.core.document import Document
from mapchar.core.font import Font, TextBox
from mapchar.core.table import Table


class EntryKind(Enum):
    FILE = "file"
    BLOCK = "block"
    BOOKMARK = "bookmark"
    TABLE = "table"
    FONT = "font"


@dataclass
class EntrySession:
    """Per-entry choices captured when leaving it."""

    table_id: str | None = None
    offset: int = 0
    view: str = "raw"
    """``raw`` or ``strings``."""


@dataclass(eq=False)
class Entry:
    kind: EntryKind
    name: str
    path: str | None = None
    extra_paths: tuple[str, ...] = ()
    container_id: str = "raw"
    compression_id: str | None = None
    config: BlockConfig | None = None
    """Blocks only."""
    parent: Entry | None = None
    """Blocks and bookmarks: the file they belong to."""
    bookmark_offset: int = 0
    slice_offset: int = 0
    """Blocks with their own compression: where the compressed data starts."""
    slice_length: int = 0
    """Its compressed length; 0 means "found on first read"."""
    spare_room: str = "fill"
    """What fills a slot a shorter re-compression leaves: ``fill`` or ``keep``."""
    font: Font | None = None
    """Font entries: the glyph sheet and its map."""
    box: TextBox | None = None
    """Blocks: the text box strings are previewed in, when bound to a font."""
    dialect: str | None = None
    """Tables: the dialect the file was read with."""
    tables: list[Table] = field(default_factory=list)
    """Tables: the loaded logical tables."""
    session: EntrySession = field(default_factory=EntrySession)
    doc: Document | None = None
    live_revision: int = 0
    saved_revision: int = 0
    missing: bool = False

    @property
    def paths(self) -> tuple[str, ...]:
        return ((self.path,) if self.path else ()) + self.extra_paths

    @property
    def dirty(self) -> bool:
        return self.live_revision != self.saved_revision

    @property
    def is_child(self) -> bool:
        return self.kind in (EntryKind.BLOCK, EntryKind.BOOKMARK)


def normalize_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class Workspace:
    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.current: Entry | None = None
        self._revisions = count(1)
        self.on_added: list[Callable[[Entry], None]] = []
        self.on_removed: list[Callable[[Entry], None]] = []
        self.on_reset: list[Callable[[], None]] = []
        self.on_current_changed: list[Callable[[Entry | None], None]] = []
        self.on_dirty_changed: list[Callable[[Entry], None]] = []

    # --- revisions -----------------------------------------------------

    def next_revision(self) -> int:
        return next(self._revisions)

    def stamp(self, entry: Entry, revision: int | None = None) -> None:
        entry.live_revision = revision if revision is not None else self.next_revision()
        self._fire(self.on_dirty_changed, entry)

    def mark_saved(self, entry: Entry) -> None:
        entry.saved_revision = entry.live_revision
        self._fire(self.on_dirty_changed, entry)

    # --- lookup --------------------------------------------------------

    def _find(self, kind: EntryKind, path: str) -> Entry | None:
        key = normalize_path(path)
        for e in self.of_kind(kind):
            if e.path and normalize_path(e.path) == key:
                return e
        return None

    def find_file(self, path: str) -> Entry | None:
        return self._find(EntryKind.FILE, path)

    def find_table(self, path: str) -> Entry | None:
        return self._find(EntryKind.TABLE, path)

    def entry_by_id(self, key: object) -> Entry | None:
        """The entry whose ``id()`` is ``key``: how a tree item names one."""
        return next((e for e in self.entries if id(e) == key), None)

    def entry_for_table(self, table_id: str) -> Entry | None:
        """The table entry one of whose tables is ``table_id``."""
        for e in self.of_kind(EntryKind.TABLE):
            if any(t.id == table_id for t in e.tables):
                return e
        return None

    def children(self, parent: Entry) -> list[Entry]:
        return [e for e in self.entries if e.parent is parent]

    def of_kind(self, kind: EntryKind) -> list[Entry]:
        return [e for e in self.entries if e.kind is kind]

    def files(self) -> list[Entry]:
        return self.of_kind(EntryKind.FILE)

    def fonts(self) -> list[Entry]:
        return self.of_kind(EntryKind.FONT)

    def table_entries(self) -> list[Entry]:
        return self.of_kind(EntryKind.TABLE)

    def tables(self) -> dict[str, Table]:
        """Every loaded table by id, across table entries."""
        out: dict[str, Table] = {}
        for e in self.of_kind(EntryKind.TABLE):
            for t in e.tables:
                out[t.id] = t
        return out

    # --- lifecycle -----------------------------------------------------

    def add(self, entry: Entry, index: int | None = None) -> Entry:
        if index is None or index > len(self.entries):
            if entry.parent is not None and entry.parent in self.entries:
                # Keep children right after their parent's last child.
                index = self.entries.index(entry.parent) + 1
                while (
                    index < len(self.entries)
                    and self.entries[index].parent is entry.parent
                ):
                    index += 1
            else:
                index = len(self.entries)
        self.entries.insert(index, entry)
        self._fire(self.on_added, entry)
        return entry

    def new_file(self, path: str, name: str | None = None, **fields) -> Entry:
        """A file entry for ``path``, not yet added; it is named after the file."""
        return Entry(EntryKind.FILE, name or os.path.basename(path), path, **fields)

    def open_file(self, path: str, name: str | None = None, **fields) -> Entry:
        """The entry already open on ``path``, else a new one, added."""
        existing = self.find_file(path)
        if existing is not None:
            return existing
        return self.add(self.new_file(path, name, **fields))

    def close(self, entry: Entry) -> list[Entry]:
        """Remove an entry and its children; returns what was removed."""
        removed = [c for c in self.entries if c.parent is entry] + [entry]
        for e in removed:
            if e in self.entries:
                self.entries.remove(e)
                self._fire(self.on_removed, e)
        if self.current in removed:
            self.set_current(self._neighbour(entry))
        return removed

    def _neighbour(self, entry: Entry) -> Entry | None:
        candidates = [e for e in self.entries if e.kind is not EntryKind.BOOKMARK]
        return candidates[0] if candidates else None

    def reorder(self, entry: Entry, new_index: int) -> None:
        group = [entry] + self.children(entry)
        for e in group:
            self.entries.remove(e)
        new_index = max(0, min(new_index, len(self.entries)))
        self.entries[new_index:new_index] = group
        self._fire(self.on_reset)

    def replace(self, entries: list[Entry], current: Entry | None) -> None:
        self.entries = list(entries)
        self.current = current
        self._fire(self.on_reset)
        self._fire(self.on_current_changed, current)

    def set_current(self, entry: Entry | None) -> None:
        if entry is self.current:
            return
        self.current = entry
        self._fire(self.on_current_changed, entry)

    def invalidate_extractions(self) -> None:
        """Make every loaded document extract its strings again."""
        for e in self.entries:
            if e.doc is not None:
                e.doc.extraction_key = None

    def _fire(self, callbacks, *args) -> None:
        for cb in list(callbacks):
            cb(*args)
