"""The entry: one open row, its shape, and the pure rules over a list of them."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from mapchar.core.block import BlockConfig, Status, StringRecord
from mapchar.core.capabilities import EntryKind
from mapchar.core.document import Document
from mapchar.core.font import TextBox
from mapchar.core.notices import Notice
from mapchar.core.table import Table, TableSet
from mapchar.pipeline.extract import split_run_text


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


def unjoined_states(
    saved: dict[int, StringState],
    strings: list[StringRecord],
    config: BlockConfig,
    tables: TableSet,
) -> dict[int, StringState]:
    """``saved`` — the state of a block whose pointers reach runs, kept by a
    project from before version 3 with each run as one string — as the state
    of the ``strings`` the block reads as now, one to each end token.

    The old strings are found again by counting: a pointer block's began where
    a string with pointers does, and a range's held
    :attr:`~mapchar.core.block.BlockConfig.strings_per_pointer` of today's
    each. A run's text is cut at its end codes
    (:func:`~mapchar.pipeline.extract.split_run_text`), its mark goes to every
    string of it and its notes to the first. The digest was of the whole run,
    so each original goes by its text until its bytes are seen to say it.
    """
    runs: list[list[StringRecord]] = []
    per = max(config.strings_per_pointer, 1)
    for n, rec in enumerate(strings):
        begins = bool(rec.pointers) if config.has_pointers else n % per == 0
        if begins or not runs:
            runs.append([])
        runs[-1].append(rec)
    out: dict[int, StringState] = {}
    for old, run in enumerate(runs):
        st = saved.get(old)
        if st is None:
            continue
        originals = split_run_text(st.original, tables) if st.original else []
        translations = split_run_text(st.translation, tables) if st.translation else []
        for k, rec in enumerate(run):
            out[rec.index] = StringState(
                originals[k] if k < len(originals) else None,
                st.status,
                st.notes if k == 0 else "",
                translations[k] if k < len(translations) else None,
            )
    return out


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
    strings_mode: bool = False
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
    slot_offset: int = 0
    """Blocks with their own compression: where the compressed data starts."""
    slot_length: int | None = None
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
    runs_joined: bool = False
    """Blocks: :attr:`pending_strings` hold each pointer's run as one string,
    as a project from before version 3 does; the next extraction gives every
    string of the run its own (:func:`unjoined_states`)."""
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


def reordered(entries: list[Entry], entry: Entry, before: Entry | None) -> list[Entry]:
    """``entries`` with ``entry`` and what it holds lifted out and put back
    in front of ``before``.

    With no ``before`` the run goes last among the rows under the row that
    holds ``entry`` — its folder, else its file — so the list keeps its
    :func:`tree_order`; a row with no holder goes to the end of the list.
    """
    return moved(entries, [entry], holder(entry), before)


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
    """What a file is known by: absolute, separators normalised, and case folded
    where the file system folds it.

    The same file reaches us spelled differently depending on how it was opened,
    so two entries — or two rows of Open Recent — naming one file must answer to
    one key.
    """
    return os.path.normcase(os.path.abspath(path))
