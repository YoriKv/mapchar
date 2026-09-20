"""The open-entries model: what is open, which is current, what is unsaved."""

from __future__ import annotations

import os
from collections import ChainMap
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from itertools import count

from mapchar.core.capabilities import EntryKind
from mapchar.core.numbers import clamp
from mapchar.core.table import Table
from mapchar.project.entry import (
    CHILD_KINDS,
    STRING_DATA,
    Entry,
    holder,
    normalize_path,
    within,
)
from mapchar.project.glossary import GlossaryTerm


class Workspace:
    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.current: Entry | None = None
        self._revisions = count(1)
        self.on_added: list[Callable[[Entry], None]] = []
        self.on_removed: list[Callable[[Entry], None]] = []
        self.on_reset: list[Callable[[], None]] = []
        self.on_rows_changed: list[Callable[[], None]] = []
        """Rows came or went: once for an :meth:`add` or a :meth:`close`,
        however many rows the close took, and once for a whole :meth:`batch`
        — what a listener rebuilding from ``entries`` subscribes to, where
        ``on_added`` and ``on_removed`` fire per row."""
        self._batching = 0
        self._rows_pending = False
        self.on_current_changed: list[Callable[[Entry | None], None]] = []
        self.on_dirty_changed: list[Callable[[Entry], None]] = []
        self.builtin_tables: Mapping[str, Table] = {}
        """The standard encodings as tables, under the loaded ones: never entries,
        never saved, and built by whoever holds the registry."""
        self.glossary: list[GlossaryTerm] = []
        """The project's terms and their translations; saved with the project,
        and swapped by whoever swaps the entries."""

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

    def set_revisions(self, entry: Entry, live: int, saved: int) -> None:
        """Put both tokens back as a command captured them: an undone write is
        unsaved again by the same pair it had before the write."""
        before = entry.dirty
        entry.live_revision = live
        entry.saved_revision = saved
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

    def block_named(self, name: str) -> Entry | None:
        """The block called ``name``, if there is one.

        The name is how everything outside the project addresses a block: an
        exchange file names the block its strings belong to, and nothing else
        of the entry is in the file.
        """
        return next(
            (e for e in self.entries if e.kind is EntryKind.BLOCK and e.name == name),
            None,
        )

    def entry_for_table(self, table_id: str) -> Entry | None:
        """The table entry whose table is ``table_id``."""
        for e in self.of_kind(EntryKind.TABLE):
            if e.table is not None and e.table.id == table_id:
                return e
        return None

    def children(self, parent: Entry) -> list[Entry]:
        """Every row of a file, folders and what they hold included."""
        return [e for e in self.entries if e.parent is parent]

    def descendants(self, entry: Entry) -> list[Entry]:
        """What goes with ``entry`` when it is removed, moved or copied: a
        file's rows, or a folder's contents at every depth, in list order."""
        return [e for e in self.entries if e is not entry and within(e, entry)]

    def contents(self, container: Entry) -> list[Entry]:
        """The rows directly under a file or a folder, in list order."""
        return [e for e in self.entries if holder(e) is container]

    def blocks_of(self, file_entry: Entry, *, loaded: bool = False) -> list[Entry]:
        """The blocks under ``file_entry`` — a file, or a folder at any depth —
        in list order.

        A file's children are its blocks, its bookmarks and its folders; almost
        everything that walks them wants the blocks alone. ``loaded`` narrows
        that to the ones holding a document, which is what anything reading or
        writing their bytes means by a block.
        """
        rows = (
            self.children(file_entry)
            if file_entry.kind is EntryKind.FILE
            else self.descendants(file_entry)
        )
        return [
            e
            for e in rows
            if e.kind is EntryKind.BLOCK and (not loaded or e.doc is not None)
        ]

    def entries_sharing(self, entry: Entry) -> list[Entry]:
        """Every loaded entry whose document holds the same bytes as ``entry``'s.

        A plain block reads its file's buffer, with every other plain block on
        that file; a compressed *block* reads its slot's payload, with every
        other block over the same slot. A file's own compression is the whole
        file's — the pipeline decodes it on the way in and its buffer is the
        result — so a compressed file is still read with its plain blocks and
        never with a slot.

        Only entries with a document are named, so ``entry`` itself is in the
        list exactly when it is loaded.
        """
        if entry.kind is EntryKind.BLOCK and entry.compression_id:
            slot = (entry.compression_id, entry.slot_offset)
            return [
                e
                for e in self.of_kind(EntryKind.BLOCK)
                if e.doc is not None
                and e.parent is entry.parent
                and (e.compression_id, e.slot_offset) == slot
            ]
        file_entry = entry.parent if entry.parent is not None else entry
        shared = [file_entry] if file_entry.doc is not None else []
        shared += [
            b for b in self.blocks_of(file_entry, loaded=True) if not b.compression_id
        ]
        return shared

    def of_kind(self, kind: EntryKind) -> list[Entry]:
        return [e for e in self.entries if e.kind is kind]

    def blocks_on_tables(self, entries: list[Entry]) -> list[Entry]:
        """The blocks whose start table is in one of the table entries ``entries``.

        What a table's removal leaves behind: the block keeps its strings and
        its configuration, but nothing can decode the bytes again until the
        table is back, so the removal has to say so before it happens.
        """
        going = {
            e.table.id
            for e in entries
            if e.kind is EntryKind.TABLE and e.table is not None
        }
        if not going:
            return []
        return [
            b
            for b in self.of_kind(EntryKind.BLOCK)
            if b.config is not None and b.config.table_id in going
        ]

    def drop_clean_documents(self) -> int:
        """Forget every cached document so a new registry re-reads it — except
        the ones holding unsaved edits. Returns how many were kept.

        A refresh is not a revert: edits live only in the document, so dropping
        a dirty one throws work away while the entry still reads as edited.
        Those keep what they have, and re-read when the user next writes or
        closes them. A dirty entry's parents are
        kept too: a block settles its bytes through its file's buffer, so
        re-reading that from disk underneath it would strand the edits.
        """
        keep: set[int] = set()
        for entry in self.entries:
            if not entry.dirty:
                continue
            node: Entry | None = entry
            while node is not None:
                keep.add(id(node))
                node = node.parent
        for entry in self.entries:
            if id(entry) not in keep:
                self.drop_document(entry)
        return sum(1 for entry in self.entries if entry.dirty)

    def files(self) -> list[Entry]:
        return self.of_kind(EntryKind.FILE)

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
            above = holder(entry)
            if above is not None and above in self.entries:
                # Keep a row right after the last row its folder or file holds.
                index = self.entries.index(above) + 1
                while index < len(self.entries) and within(self.entries[index], above):
                    index += 1
            else:
                index = len(self.entries)
        self.entries.insert(index, entry)
        self._fire(self.on_added, entry)
        self._rows_changed()
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
        """Remove an entry and what it holds; returns what was removed."""
        removed = self.descendants(entry) + [entry]
        anchor = min(
            (self.entries.index(e) for e in removed if e in self.entries), default=0
        )
        for e in removed:
            if e in self.entries:
                self.entries.remove(e)
                self._fire(self.on_removed, e)
        self._rows_changed()
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
        Bookmarks and folders are skipped — they can never be current.
        """
        # A folder's rows are String Data, and a folder is never shown itself.
        group = STRING_DATA if kind in STRING_DATA | CHILD_KINDS else {kind}
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
        group = [entry] + self.descendants(entry)
        for e in group:
            self.entries.remove(e)
        new_index = clamp(new_index, 0, len(self.entries))
        self.entries[new_index:new_index] = group
        self._fire(self.on_reset)

    def layout(self) -> list[tuple[Entry, Entry | None]]:
        """The list's order with each row's folder: what a move between
        folders changes, and what its undo puts back."""
        return [(e, e.folder) for e in self.entries]

    def arrange(self, layout: list[tuple[Entry, Entry | None]]) -> None:
        """Lay the list out as :meth:`layout` captured it, folders and order
        together, as one reset."""
        for entry, folder in layout:
            entry.folder = folder
        self.replace([e for e, _ in layout], self.current)

    def replace(self, entries: list[Entry], current: Entry | None) -> None:
        """Swap the whole list for ``entries`` — a loaded project replaces the
        workspace, never merges into it.

        The old list goes as one ``on_reset`` over an *empty* list, so a listener
        that rebuilds from ``entries`` tears down rather than re-reading rows
        that are about to go; the new list arrives as a second ``on_reset`` over
        the whole of it, not an ``on_added`` per entry — a listener rebuilding
        on each would rebuild a project of hundreds of blocks hundreds of
        times. ``current`` is set last, so the activation lands on a populated
        list.
        """
        self.set_current(None)
        self.entries.clear()
        self._fire(self.on_reset)
        self.entries.extend(entries)
        self._fire(self.on_reset)
        self.set_current(current)

    def set_current(self, entry: Entry | None) -> None:
        if entry is self.current:
            return
        # A bookmark is a place in another entry and a folder a group of rows,
        # never a thing to show.
        assert entry is None or entry.kind not in (EntryKind.BOOKMARK, EntryKind.FOLDER)
        assert entry is None or entry in self.entries
        self.current = entry
        self._fire(self.on_current_changed, entry)

    def invalidate_extractions(self) -> None:
        """Make every loaded document extract its strings again."""
        for e in self.entries:
            if e.doc is not None:
                e.doc.extraction_key = None

    def drop_document(self, entry: Entry, *, reconfigured: bool = False) -> None:
        """Discard an entry's cached document, keeping its string state.

        Once a load has consumed :attr:`Entry.pending_strings` the document is
        the only place the originals, statuses and notes exist, so a drop that
        did not stash them back would lose them. An extracted document is the
        newer answer; a pending set the extraction never consumed is still the
        only one there is. ``reconfigured`` is :meth:`Entry.stash_strings`'s.
        """
        entry.stash_strings(reconfigured=reconfigured)
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

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Hold ``on_rows_changed`` until the end, and fire it once then if
        any row came or went — for a paste, a removal or its undo that adds or
        closes many rows one at a time."""
        self._batching += 1
        try:
            yield
        finally:
            self._batching -= 1
            if not self._batching and self._rows_pending:
                self._rows_pending = False
                self._fire(self.on_rows_changed)

    def _rows_changed(self) -> None:
        if self._batching:
            self._rows_pending = True
        else:
            self._fire(self.on_rows_changed)

    def _fire(self, callbacks, *args) -> None:
        for cb in list(callbacks):
            cb(*args)
