"""The open-entries model: what is open, which is current, what is unsaved."""

from __future__ import annotations

import os
from collections import ChainMap
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count

from mapchar.core.block import BlockConfig, Status
from mapchar.core.capabilities import EntryKind
from mapchar.core.document import Document
from mapchar.core.font import TextBox
from mapchar.core.notices import Notice
from mapchar.core.table import Table
from mapchar.project.glossary import GlossaryTerm


@dataclass
class StringState:
    """One string's saved state, apart from any document.

    What the project file stores per string — its original, its status and its
    notes — and what an entry carries while no document of its own exists to
    hold it (:attr:`Entry.pending_strings`).
    """

    original: str | None = None
    status: Status = Status.UNTOUCHED
    notes: str = ""
    translation: str | None = None
    """A translation a project written before originals were kept was still
    holding, not yet in the ROM. The next extraction puts it into the bytes
    and it is gone."""
    digest: int | None = None
    """The checksum of the original's bytes
    (:attr:`~mapchar.core.block.StringRecord.original_digest`); ``None`` in a
    project written before it was kept."""
    extent: tuple[int, int] | None = None
    """The bits the string covered when its block was last re-configured.

    Set by the stash a block edit makes: a string the new reading cuts at
    other bits is not the same string, so its original is taken afresh from
    the bytes rather than kept.
    """


def string_state(rec, extent: bool = False) -> StringState:
    """``rec``'s state as an entry carries it, its extent when asked."""
    return StringState(
        rec.original,
        rec.status,
        rec.notes,
        digest=rec.original_digest,
        extent=(rec.start_bit, rec.end_bit) if extent else None,
    )


@dataclass
class EntrySession:
    """Per-entry choices captured when leaving it."""

    table_id: str | None = None
    offset: int = 0
    view: str = "raw"
    """Which view the entry was left in: ``raw``, ``text`` or ``strings``."""
    config: BlockConfig | None = None
    """A file's reading, as the top bar sets it: what New Block starts from.
    A block's own is :attr:`Entry.config`."""
    resolve_pointers: bool = False
    """Read as pointers, show the string each reaches in its place."""
    preview_scheme: str | None = None
    """What the Decompressed view previews a file through, as its picker sets
    it: a compression plugin's id, ``""`` for none, or ``None`` — the default —
    for automatic, which arms whichever registered scheme's signature the view
    has landed on. A file's, like :attr:`config`; a block reads through its own
    :attr:`Entry.compression_id` and previews nothing."""
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
    """Blocks, bookmarks and folders: the file they belong to."""
    folder: Entry | None = None
    """Blocks, bookmarks and folders: the folder they sit in under their file,
    ``None`` for a row directly under it.

    Only where the row is shown: a folder has the same :attr:`parent` as what
    it holds, and moving a row between folders changes nothing else about it.
    """
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
    room: int | None = None
    """Blocks: the exclusive end this block's text reached before a write
    shortened it; ``None`` until one does.

    State, not configuration: it is the block's own extent, remembered so that
    the room a shortened string gave up is still the block's to take back
    (:func:`~mapchar.core.block.remembered_room`). A change to how the block is
    read forgets it, the strings being cut afresh."""
    box: TextBox | None = None
    """Blocks: the text box strings are previewed in."""
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
    table_id: str | None = None
    """Tables: the id the project gives the table in place of its file's,
    set by Rename Table; ``None`` while the file's own applies."""
    table_includes: tuple[str, ...] | None = None
    """Tables: the ``@include`` list the project gives the table in place of
    its file's — set in the Table Editor, or by renaming a table it includes;
    ``None`` while the file's own applies."""
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
    """Blocks: string state with no document to live in yet.

    Set by a project load and by dropping a document, and consumed by the next
    extraction. It is also what a save serialises when the block was never
    opened, so state survives a session that never looked at it.
    """
    fixed_ends_shown: bool = False
    """Blocks: :attr:`pending_strings` spell a fixed string's end token, as a
    project from before version 2 does; the next extraction respells them
    (:func:`~mapchar.pipeline.extract.respell_fixed_end`)."""
    live_revision: int = 0
    saved_revision: int = 0
    missing: bool = False
    strings_cache: tuple | None = field(default=None, repr=False)
    """Blocks: the last serialisation of this block's strings, with the state it
    was made from (:func:`~mapchar.project.projectfile._string_records`).

    A project's dirty check serialises the whole project after every commit and
    every status change, and a block's strings are nearly all of it. Building
    the state the records are made of costs a tuple per string; building the
    records costs a dictionary per string and serialising them costs more
    again, so a block whose strings are as they were hands back the very list
    it handed back last time, and the caller can tell by its identity that
    nothing of it has to be written out afresh.
    """

    @property
    def paths(self) -> tuple[str, ...]:
        return ((self.path,) if self.path else ()) + self.extra_paths

    @property
    def dirty(self) -> bool:
        return self.live_revision != self.saved_revision

    @property
    def is_child(self) -> bool:
        """Whether the row belongs to a file: a block, a bookmark or a folder."""
        return self.kind in CHILD_KINDS

    def stash_strings(
        self, doc: Document | None = None, *, reconfigured: bool = False
    ) -> None:
        """Copy this block's string state onto the entry, where no document
        holds it.

        The safety net under everything that drops or fails to build a document
        — a block edit, an unavailable table, a refused extraction — so the next
        successful read puts the same originals, statuses and notes back on the
        same indices. Merged into whatever is already stashed, since a block may
        go through several such rounds before it reads again.

        ``reconfigured`` says the block's reading is changing: each state then
        remembers the bits its string covered, and a string the new reading
        cuts differently takes its original afresh from the bytes.
        """
        doc = doc if doc is not None else self.doc
        if doc is None or not doc.strings:
            return
        saved = dict(self.pending_strings or {})
        for rec in doc.strings:
            saved[rec.index] = string_state(rec, extent=reconfigured)
        self.pending_strings = saved or None


CHILD_KINDS = frozenset({EntryKind.BLOCK, EntryKind.BOOKMARK, EntryKind.FOLDER})
"""The kinds that sit under a file in the Files panel."""


def has_edits(entry: Entry) -> bool:
    """Whether the block holds work a fresh cut would take with it: a string
    that is no longer its original, or room a shortened string gave up.

    A block whose strings are not read yet — never opened, or waiting on the
    read a block edit left it — is told by the state it carries instead, where
    any status but *untouched* counts: the project keeps one status per string,
    so a translated string marked *review* or *done* says only that.
    """
    if entry.room is not None:
        return True
    if entry.doc is not None and entry.doc.strings:
        return any(rec.edited for rec in entry.doc.strings)
    return any(
        state.status is not Status.UNTOUCHED
        for state in (entry.pending_strings or {}).values()
    )


def within(entry: Entry, container: Entry) -> bool:
    """Whether ``entry`` sits inside ``container``: any row of a file, or any
    row of a folder at any depth."""
    if entry.parent is container:
        return True
    folder = entry.folder
    while folder is not None:
        if folder is container:
            return True
        folder = folder.folder
    return False


def holder(entry: Entry) -> Entry | None:
    """The row ``entry`` is shown under: its folder, else its file."""
    return entry.folder if entry.folder is not None else entry.parent


def tree_order(entries: list[Entry]) -> list[Entry]:
    """``entries`` with every row straight after the row holding it, in the
    order the list already had.

    The order the workspace keeps: a file's rows follow it and a folder's
    contents follow the folder, so a row and everything it holds are one run
    of the list, which is what lifting one out and putting it back relies on.
    A row whose holder is not in the list stands on its own.
    """
    present = {id(e) for e in entries}
    held: dict[int, list[Entry]] = {}
    roots: list[Entry] = []
    for entry in entries:
        above = holder(entry)
        if above is not None and id(above) in present and above is not entry:
            held.setdefault(id(above), []).append(entry)
        else:
            roots.append(entry)
    out: list[Entry] = []
    seen: set[int] = set()
    stack = list(reversed(roots))
    while stack:
        entry = stack.pop()
        if id(entry) in seen:
            continue
        seen.add(id(entry))
        out.append(entry)
        stack.extend(reversed(held.get(id(entry), ())))
    # Rows caught in a loop of folders hold each other and reach no root.
    out += [e for e in entries if id(e) not in seen]
    return out


def free_name(name: str, taken, pattern: str = "{name} ({n})") -> str:
    """``name``, numbered up (``name (2)``) until it is not in ``taken``.

    Blocks and bookmarks are named uniquely: a dump, a translator file and an
    Atlas script all name a string by its block, so two blocks called the same
    could not be told apart on the way back in. ``pattern`` spells the numbered
    form for callers that number up some other way — a table id has no room for
    a space or brackets (:func:`~mapchar.project.tables.free_table_id`).
    """
    taken = set(taken)
    if name not in taken:
        return name
    n = 2
    while pattern.format(name=name, n=n) in taken:
        n += 1
    return pattern.format(name=name, n=n)


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
            slot = (entry.compression_id, entry.slice_offset)
            return [
                e
                for e in self.of_kind(EntryKind.BLOCK)
                if e.doc is not None
                and e.parent is entry.parent
                and (e.compression_id, e.slice_offset) == slot
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

    @staticmethod
    def reordered(
        entries: list[Entry], entry: Entry, before: Entry | None
    ) -> list[Entry]:
        """``entries`` with ``entry`` and what it holds lifted out and put back
        in front of ``before``.

        With no ``before`` the run goes last among the rows under the row that
        holds ``entry`` — its folder, else its file — so the list keeps its
        :func:`tree_order`; a row with no holder goes to the end of the list.
        """
        return Workspace.moved(entries, [entry], holder(entry), before)

    @staticmethod
    def moved(
        entries: list[Entry],
        rows: list[Entry],
        container: Entry | None,
        before: Entry | None,
    ) -> list[Entry]:
        """``entries`` with ``rows`` and what they hold lifted out and put back,
        in the order they had, in front of ``before`` — or, with no ``before``,
        last among what ``container`` holds, else at the end of the list.

        One pass however many rows move, so grouping a whole selection costs
        what moving one row does.
        """
        ids = {id(r) for r in rows}

        def lifted(e: Entry) -> bool:
            if id(e) in ids or id(e.parent) in ids:
                return True
            folder = e.folder
            while folder is not None:
                if id(folder) in ids:
                    return True
                folder = folder.folder
            return False

        group: list[Entry] = []
        rest: list[Entry] = []
        for e in entries:
            (group if lifted(e) else rest).append(e)
        if before is not None and before in rest:
            at = rest.index(before)
        else:
            at = len(rest)
            if container is not None and container in rest:
                at = rest.index(container) + 1
                while at < len(rest) and within(rest[at], container):
                    at += 1
        return rest[:at] + group + rest[at:]

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
        new_index = max(0, min(new_index, len(self.entries)))
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

    Rewrites an entry's ``path`` and any of its ``extra_paths`` naming the same
    file — so relocating a shared ROM fixes the file and the blocks and
    bookmarks under it together.
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
