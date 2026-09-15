"""The open-entries model: what is open, which is current, what is unsaved."""

from __future__ import annotations

import os
from collections import ChainMap
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from itertools import count

from mapchar.core.block import BlockConfig, Status
from mapchar.core.capabilities import EntryKind
from mapchar.core.document import Document
from mapchar.core.font import Font, TextBox
from mapchar.core.notices import Notice
from mapchar.core.table import Table


@dataclass
class StringState:
    """One string's saved translation state, apart from any document.

    What the project file stores per string, and what an entry carries while no
    document of its own exists to hold it (:attr:`Entry.pending_strings`).
    """

    translation: str | None = None
    status: Status = Status.UNTOUCHED
    notes: str = ""


@dataclass
class EntrySession:
    """Per-entry choices captured when leaving it."""

    table_id: str | None = None
    offset: int = 0
    view: str = "raw"
    """``raw`` or ``strings``."""
    config: BlockConfig | None = None
    """A file's reading, as the top bar sets it: what New Block starts from.
    A block's own is :attr:`Entry.config`."""
    resolve_pointers: bool = False
    """Read as pointers, show the string each reaches in its place."""
    string_view: bool = False
    """A block left in its Strings mode — all its strings as text rather than
    its source — which coming back to it takes up again. One string opened
    from the Files panel is a visit laid over the mode, not the mode. Not
    saved."""
    set_aside: BlockConfig | None = None
    """The reading the entry had before its last switch of mode, whose source
    and string type switching back restores. Not saved."""


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
    slice_length: int | None = None
    """Its compressed length; ``None`` when nobody recorded one.

    Distinct from zero, which is a slot with no room in it. Unknown means the
    slot runs to the end of the parent's buffer, and that is what bounds a
    write-back — never widened from a decode, which stops where its input did
    rather than where the slot does.
    """
    spare_room: str = "fill"
    """What fills a slot a shorter re-compression leaves: ``fill`` or ``keep``."""
    font: Font | None = None
    """Font entries: the glyph sheet and its map."""
    box: TextBox | None = None
    """Blocks: the text box strings are previewed in, when bound to a font."""
    dialect: str | None = None
    """Tables: the dialect the file was read with."""
    table: Table | None = None
    """Tables: the loaded table, the file's plus the overlay; ``None`` until read."""
    file_table: Table | None = None
    """Tables: what the file alone gave, before any in-app edit.

    The baseline :func:`~mapchar.project.tables.overlay_of` measures the overlay
    against, kept apart from :attr:`table` so the file on disk stays the file on
    disk. ``None`` for a table entry with no file, whose every entry is
    therefore an addition the project carries whole.
    """
    table_overlay: dict[str, str | None] = field(default_factory=dict)
    """Tables: the in-app edits over the file.

    Per entry key: the entry's line in the native grammar for one added or
    changed, and ``None`` for one removed. It is what the project file stores
    instead of rewriting the table file, so a file other tools read keeps
    working; **Save As File** folds it back in and empties it.
    """
    notices: tuple[Notice, ...] = ()
    """Tables: what reading the file had to say about it.

    Set by every read of the file — the open, a reload from disk, a project
    load — and shown on the Table Editor's status line, which is where a
    conversion from a legacy dialect reports what it could not keep.
    """
    session: EntrySession = field(default_factory=EntrySession)
    doc: Document | None = None
    pending_strings: dict[int, StringState] | None = None
    """Blocks: translation state with no document to live in yet.

    Set by a project load and by dropping a document, and consumed by the next
    extraction. It is also what a save serialises when the block was never
    opened, so state survives a session that never looked at it.
    """
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


def free_name(name: str, taken) -> str:
    """``name``, numbered up (``name (2)``) until it is not in ``taken``.

    Blocks and bookmarks are named uniquely: a dump, a translator file and an
    Atlas script all name a string by its block, so two blocks called the same
    could not be told apart on the way back in.
    """
    taken = set(taken)
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


NAMED_UNIQUELY = (EntryKind.BLOCK, EntryKind.BOOKMARK)
"""The kinds :func:`free_name` applies to; a file or table row keeps the name
of its file."""

STRING_DATA = frozenset({EntryKind.FILE, EntryKind.BLOCK})
"""The Files panel's String Data group: the entries the hex and text panels
show."""


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
        self.builtin_tables: Mapping[str, Table] = {}
        """The standard encodings as tables, under the loaded ones: never entries,
        never saved, and built by whoever holds the registry."""

    # --- revisions -----------------------------------------------------

    def next_revision(self) -> int:
        return next(self._revisions)

    def stamp(self, entry: Entry, revision: int | None = None) -> None:
        before = entry.dirty
        entry.live_revision = revision if revision is not None else self.next_revision()
        if entry.dirty != before:
            self._fire(self.on_dirty_changed, entry)

    def mark_saved(self, entry: Entry) -> None:
        before = entry.dirty
        entry.saved_revision = entry.live_revision
        if entry.dirty != before:
            self._fire(self.on_dirty_changed, entry)

    def dirty_entries(self) -> list[Entry]:
        """Every entry with unsaved edits, in list order."""
        return [e for e in self.entries if e.dirty]

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
        """The table entry whose table is ``table_id``."""
        for e in self.of_kind(EntryKind.TABLE):
            if e.table is not None and e.table.id == table_id:
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

    def loaded_tables(self) -> dict[str, Table]:
        """Every loaded table by id, across table entries."""
        return {
            e.table.id: e.table
            for e in self.of_kind(EntryKind.TABLE)
            if e.table is not None
        }

    def tables(self) -> Mapping[str, Table]:
        """Every table a block can read through: the loaded ones, then the
        built-in encodings (:attr:`builtin_tables`) no loaded table shadows."""
        return ChainMap(self.loaded_tables(), self.builtin_tables)

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
        anchor = min(
            (self.entries.index(e) for e in removed if e in self.entries), default=0
        )
        for e in removed:
            if e in self.entries:
                self.entries.remove(e)
                self._fire(self.on_removed, e)
        if self.current in removed:
            self.set_current(self._neighbour(anchor, entry.kind))
        return removed

    def _neighbour(self, anchor: int, kind: EntryKind) -> Entry | None:
        """Where ``current`` goes when the entry holding it closed: the nearest
        showable row of the same group after the hole, else the nearest before
        it, else a String Data entry.

        The row that took the closed one's place is the one the eye is already
        on; falling back to the end of the list only happens when nothing
        follows. The group is kept because the views show one kind of thing:
        closing a block must not swap the hex and text panels for a table.
        Bookmarks are skipped — they can never be current.
        """
        group = STRING_DATA if kind in STRING_DATA else {kind}
        after = self.entries[anchor:]
        before = list(reversed(self.entries[:anchor]))
        for kinds in (group, STRING_DATA):
            found = next(
                (e for e in after if e.kind in kinds),
                next((e for e in before if e.kind in kinds), None),
            )
            if found is not None:
                return found
        return None

    def reorder(self, entry: Entry, new_index: int) -> None:
        group = [entry] + self.children(entry)
        for e in group:
            self.entries.remove(e)
        new_index = max(0, min(new_index, len(self.entries)))
        self.entries[new_index:new_index] = group
        self._fire(self.on_reset)

    def replace(self, entries: list[Entry], current: Entry | None) -> None:
        """Swap the whole list for ``entries`` — a loaded project replaces the
        workspace, never merges into it.

        The old list goes as one ``on_reset`` over an *empty* list, so a listener
        that rebuilds from ``entries`` tears down rather than re-reading rows
        that are about to go; the new list arrives as an ``on_added`` per entry,
        which is how it is built. ``current`` is set last, so the activation
        lands on a populated list.
        """
        self.set_current(None)
        self.entries.clear()
        self._fire(self.on_reset)
        self.entries.extend(entries)
        for entry in entries:
            self._fire(self.on_added, entry)
        self.set_current(current)

    def set_current(self, entry: Entry | None) -> None:
        if entry is self.current:
            return
        # A bookmark is a place in another entry, never a thing to show.
        assert entry is None or entry.kind is not EntryKind.BOOKMARK
        assert entry is None or entry in self.entries
        self.current = entry
        self._fire(self.on_current_changed, entry)

    def invalidate_extractions(self) -> None:
        """Make every loaded document extract its strings again."""
        for e in self.entries:
            if e.doc is not None:
                e.doc.extraction_key = None

    def drop_document(self, entry: Entry) -> None:
        """Discard an entry's cached document, keeping its translation state.

        Once a load has consumed :attr:`Entry.pending_strings` the document is
        the only place those translations exist, so a drop that did not stash
        them back would silently revert the block to the bytes on disk.
        """
        if entry.doc is not None and entry.doc.strings:
            states = {
                rec.index: StringState(rec.translation, rec.status, rec.notes)
                for rec in entry.doc.strings
                if rec.translation is not None
                or rec.status is not Status.UNTOUCHED
                or rec.notes
            }
            # An extracted document is the newer answer; a pending set the
            # extraction never consumed is still the only one there is.
            if states or entry.pending_strings is None:
                entry.pending_strings = states or None
        entry.doc = None

    def invalidate_path(self, path: str, keep: Entry | None = None) -> None:
        """Drop the cached documents of entries reading ``path`` (after a save).

        ``keep`` — the entry that just wrote — holds on to its own, which is
        already the bytes now on disk, and so do the blocks under it: a file and
        its blocks deposit as one write, and that write refreshes them in place.
        An entry with unsaved edits keeps its document too: that is where they
        live, and it simply stays based on the pre-save bytes until it is written
        or reloaded.
        """
        key = normalize_path(path)
        for entry in self.entries:
            if entry is keep or (keep is not None and entry.parent is keep):
                continue
            if entry.dirty:
                continue
            if entry.doc is None:
                continue
            if any(normalize_path(p) == key for p in entry.paths):
                self.drop_document(entry)

    def _fire(self, callbacks, *args) -> None:
        for cb in list(callbacks):
            cb(*args)


def missing_paths(ws: Workspace) -> list[str]:
    """Every referenced path not on disk, de-duplicated, in list order.

    De-duplicated *before* the stat: a ROM carries its blocks and bookmarks, and
    every one of them names the same file, so one shared file is one worklist
    row — located once, corrected everywhere.
    """
    seen: set[str] = set()
    result: list[str] = []
    for entry in ws.entries:
        for path in entry.paths:
            key = normalize_path(path)
            if key in seen:
                continue
            seen.add(key)
            if not os.path.exists(path):
                result.append(path)
    return result


def relocate_path(ws: Workspace, old_path: str, new_path: str) -> list[Entry]:
    """Re-point every reference to ``old_path`` at ``new_path``; the entries
    touched.

    Rewrites an entry's ``path``, any of its ``extra_paths`` naming the same
    file, and a font's own record of where its sheet came from — so relocating a
    shared ROM fixes the file and the blocks and bookmarks under it together.
    Pure data: the caller re-reads whatever was affected.
    """
    key = normalize_path(old_path)
    old_name, new_name = os.path.basename(old_path), os.path.basename(new_path)
    touched: list[Entry] = []
    for entry in ws.entries:
        changed = entry.path is not None and normalize_path(entry.path) == key
        if changed:
            entry.path = new_path
            # A row named after its file follows the file; a name the user typed
            # is theirs and survives the move.
            if entry.name == old_name:
                entry.name = new_name
            if entry.font is not None:
                entry.font = replace(entry.font, path=new_path)
        moved_extra = tuple(
            new_path if normalize_path(p) == key else p for p in entry.extra_paths
        )
        if moved_extra != entry.extra_paths:
            entry.extra_paths = moved_extra
            changed = True
        if changed:
            touched.append(entry)
    return touched


def retarget_files(ws: Workspace, entry: Entry, paths: tuple[str, ...]) -> list[Entry]:
    """Re-point a file entry at ``paths``, carrying its children; the entries
    touched.

    The file list is the entry's identity as much as its contents: ``paths[0]``
    is the row in the Files panel, the key a block or bookmark is found by, and
    the file a write is attributed to. So the children move in the same step —
    their offsets are counted against the *join*, so one left on the old list
    would address something else. A row still named after its first file follows
    the new one, the same rule :func:`relocate_path` uses. Pure data: the caller
    drops the affected documents and reads them again.
    """
    if entry.kind is not EntryKind.FILE or not paths:
        return []
    first, *rest = paths
    named_after_file = bool(entry.path) and entry.name == os.path.basename(entry.path)
    # The children are found before the path moves: they are keyed by the one
    # that is about to change.
    touched = [entry, *ws.children(entry)]
    for moved in touched:
        moved.path = first
        moved.extra_paths = tuple(rest)
    if named_after_file:
        entry.name = os.path.basename(first)
    return touched
