"""Undo commands: thin before/after pairs that call window ``apply_*`` methods.

Undo/redo is built on Qt's ``QUndoStack``/``QUndoCommand`` — a deliberate
exception to the Qt-free-model rule, because history is per-launch UI session
state and Qt's stack provides the menu actions, the merging and the obsolete
handling for free while ``core``/``pipeline``/``project`` stay Qt-free.

One **unified session stack** holds every command in chronological order — files
panel structure, per-entry configuration, view moves, string edits, hex
overtypes, table and font edits, writes to disk — so a single Ctrl+Z always
reverts the most recent action whichever surface made it. Three things follow
from that, and they are what :class:`_StateCommand` exists to state once:

- **The guard.** Applying pokes the same widgets and paths a user gesture does,
  so every apply runs inside the window's ``_undo_apply()`` guard and the push
  sites bail while it is set. An apply can then never push a second command.
- **Reach.** A command scoped to an entry has to get back to that entry before it
  can land (:class:`_CurrentEntryCommand`), and an *edit* has to get back to the
  view it was made in as well (:class:`_EditContextCommand`) — reverting a
  translation while the Hex tab is up would happen somewhere the user cannot see
  it. A change that shows wherever you are — a rename, a reorder — reaches
  nothing (:class:`_InPlaceCommand`), because yanking the view to it would be a
  surprise rather than context.
- **Revision tokens.** An entry is unsaved when its live revision differs from
  the one on disk, so an edit that minted a fresh token on the way *back* would
  leave an undone change dirty for ever. Every command over bytes or records
  therefore carries the revision on **both** sides of its pair and hands each
  side the token that belongs to it: undo restores the token the entry had
  before, and an undo back to what was written reads clean again.

``QUndoStack.push()`` runs ``redo()`` immediately, so push sites capture the
before state *first* and let the first ``redo()`` do the work — which is also why
a command can read the live revision in its own constructor.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from PySide6.QtGui import QUndoCommand

from mapchar.core.table import Table
from mapchar.pipeline.filechange import FileChange
from mapchar.project.workspace import Entry

# QUndoStack only attempts mergeWith between commands whose id() match, and -1
# never merges; any other command landing in between breaks the chain.
OFFSET_ID = 1
FIELD_ID = 2
FONT_ID = 3
BOX_ID = 4
BLOCK_ID = 5
STRINGS_ID = 6
GLOSSARY_ID = 7


class _StateCommand(QUndoCommand):
    """One entry's state moving between a captured ``before`` and ``after``.

    The shape almost every command here has: hold the pair, and in each direction
    hand the right half to one window ``apply_*`` helper inside the window's
    re-entrancy guard. Subclasses supply :meth:`_apply` and pick their reach by
    subclassing, rather than by overriding :meth:`_reach` directly. Commands
    whose two directions are not the same operation over a pair — adding versus
    removing an entry — are not this shape and stay written out.
    """

    def __init__(self, window, entry: Entry | None, text: str, before, after) -> None:
        super().__init__(text)
        self.window = window
        self.entry = entry
        self.before = before
        self.after = after

    def redo(self) -> None:
        self._run(self.after)

    def undo(self) -> None:
        self._run(self.before)

    def _run(self, state) -> None:
        with self.window._undo_apply():
            if self._reach():
                self._apply(state)

    def _reach(self) -> bool:
        """Put the window where this command's change belongs; False to skip."""
        raise NotImplementedError

    def _apply(self, state) -> None:
        """Land ``state`` — one direction of this command, on the window."""
        raise NotImplementedError


class _CurrentEntryCommand(_StateCommand):
    """A change to the entry *on screen*: undo returns to it first.

    The stack is chronological across entries, so a step made in another entry
    has to switch the view back before it can be reverted where it happened.
    """

    def _reach(self) -> bool:
        return self.window._ensure_current(self.entry)


class _InPlaceCommand(_StateCommand):
    """A change visible wherever you are, so the view never moves for it.

    A rename, a reorder, a table edit: the Files panel or the Table Editor is
    where it shows, and yanking the view to the affected entry would be a
    surprise rather than the context the change needs.
    """

    def _reach(self) -> bool:
        return True


class _EditContextCommand(_StateCommand):
    """A change made *in* a view, reverted where it was made.

    The same entry is edited from the Strings tab and from the Hex tab plus the
    Hex dock, so a step that came back in the other one would revert something
    off screen. The view — and the row or offset within it — therefore travels
    with the command alongside the entry.
    """

    def __init__(self, window, entry, text, before, after, view: str, where) -> None:
        super().__init__(window, entry, text, before, after)
        self.view = view
        self.where = where

    def _reach(self) -> bool:
        return self.window._ensure_edit_context(self.entry, self.view, self.where)


class _MergingCommand(_StateCommand):
    """A command a consecutive sibling of collapses into.

    The tail every merge here shares: take the newer command's half of the pair
    — its ``redo`` has already run, so that half is the live state — and where
    the run walked back to what it started from, drop the empty step and, for a
    command carrying revision tokens, hand the entry back the token it had
    before. A subclass says only which commands are the same run, in
    :meth:`_mergeable`.
    """

    _stamps = False
    """Whether the state is a ``(value, revision)`` pair whose token is handed
    back when the step is dropped."""

    def _mergeable(self, other) -> bool:
        """Whether ``other``, pushed straight after this one, is the same run."""
        raise NotImplementedError

    def _value(self, state):
        return state[0] if self._stamps else state

    def mergeWith(self, other) -> bool:  # noqa: N802 - Qt override
        if not self._mergeable(other):
            return False
        self.after = other.after
        if self._value(self.after) == self._value(self.before):
            self.setObsolete(True)
            if self._stamps:
                self.window.workspace.stamp(self.entry, self.before[1])
        return True


class EntryCommand(QUndoCommand):
    """Add (or remove) an entry together with its children.

    Written out rather than a :class:`_StateCommand`: its two directions are
    genuinely different operations rather than one operation over a pair, and
    what redo has to put back — the index the row sat at, the children that came
    away with it — is learnt by doing the removal.
    """

    def __init__(self, window, entry: Entry, add: bool):
        super().__init__(("Add " if add else "Remove ") + entry.name)
        self.window = window
        self.entry = entry
        self.add = add
        self.index: int | None = None
        self.children: list[Entry] = []

    def _do_add(self) -> None:
        with self.window._undo_apply():
            self.window.apply_entry_add(self.entry, self.index, self.children)

    def _do_remove(self) -> None:
        with self.window._undo_apply():
            ws = self.window.workspace
            self.index = (
                ws.entries.index(self.entry) if self.entry in ws.entries else None
            )
            removed = self.window.apply_entry_remove(self.entry)
            self.children = [e for e in removed if e is not self.entry]

    def redo(self) -> None:
        self._do_add() if self.add else self._do_remove()

    def undo(self) -> None:
        self._do_remove() if self.add else self._do_add()


class OffsetCommand(_MergingCommand, _CurrentEntryCommand):
    """A view move; consecutive moves in one entry merge."""

    def __init__(self, window, entry: Entry, before: int, after: int):
        super().__init__(window, entry, "Move view", before, after)

    def id(self) -> int:
        return OFFSET_ID

    def _mergeable(self, other) -> bool:
        # The same-entry check is load-bearing on the unified stack: moves in two
        # entries can sit adjacent and must stay separate steps.
        return isinstance(other, OffsetCommand) and other.entry is self.entry

    def _apply(self, state: int) -> None:
        self.window.apply_offset(self.entry, state)


class StringFieldCommand(_MergingCommand, _EditContextCommand):
    """One field of one string: its notes or its status.

    State is the value paired with the revision token it leaves the entry at, so
    an undo hands back the exact unsaved-state the entry had before it.

    ``run`` is what lets a run of edits on one cell collapse into a single step
    without a paste, a status toggle or a move to another row merging into it:
    the window bumps the run number when the run ends (the selection moves, the
    entry changes), and only commands sharing one merge.
    """

    def __init__(
        self,
        window,
        entry: Entry,
        index: int,
        field: str,
        before,
        after,
        *,
        run: int = 0,
    ):
        revision = entry.live_revision
        super().__init__(
            window,
            entry,
            f"Edit {field}",
            (before, revision),
            (after, revision if after == before else window.workspace.next_revision()),
            "strings",
            index,
        )
        self.index = index
        self.field = field
        self.run = run

    def id(self) -> int:
        return FIELD_ID

    _stamps = True

    def _mergeable(self, other) -> bool:
        return (
            isinstance(other, StringFieldCommand)
            and other.entry is self.entry
            and other.index == self.index
            and other.field == self.field
            and other.run == self.run
        )

    def _apply(self, state) -> None:
        value, revision = state
        self.window.apply_string_field(
            self.entry, self.index, self.field, value, revision
        )


class StringsEditCommand(_MergingCommand, _EditContextCommand):
    """A block's strings changed in place: the bytes the layout rewrote.

    A translation lives in the ROM's bytes, so editing one is a splice — of
    the whole stretch the layout touched, since a packed block moves every
    string after the edited one and rewrites their pointers. The entry is the
    block, for the reach; the revision belongs to whichever entry's buffer the
    bytes are (``StringEditMixin.apply_strings_edit``).

    A run of commits on one cell is one step, as
    :class:`StringFieldCommand`'s is: ``run`` and ``index`` say which commands
    are the same run, and merging lays the two stretches over each other. A
    run that ends with the bytes it began with is no step, and hands the owner
    its revision back.
    """

    _stamps = True

    def __init__(
        self,
        window,
        entry: Entry,
        offset: int,
        before: bytes,
        after: bytes,
        index: int | None,
        text: str = "Edit translation",
        *,
        run: int | None = None,
    ):
        owner = window._bytes_owner(entry)
        revision = owner.live_revision
        super().__init__(
            window,
            entry,
            text,
            (before, revision),
            (after, window.workspace.next_revision()),
            "strings",
            index,
        )
        self.offset = offset
        self.run = run

    def id(self) -> int:
        return STRINGS_ID

    def _mergeable(self, other) -> bool:
        if not (
            isinstance(other, StringsEditCommand)
            and other.entry is self.entry
            and other.where == self.where
            and self.run is not None
            and other.run == self.run
        ):
            return False
        # Two stretches with a gap between them would need bytes neither holds.
        return other.offset <= self.end and self.offset <= other.end

    @property
    def end(self) -> int:
        return self.offset + len(self.before[0])

    def mergeWith(self, other) -> bool:  # noqa: N802 - Qt override
        if not self._mergeable(other):
            return False
        lo = min(self.offset, other.offset)
        hi = max(self.end, other.end)
        # Before: what the union held before either — the other's stretch as
        # it was after this one, with this one's own stretch laid back over it.
        # After: this one's result, with the other's laid over it.
        before = bytearray(hi - lo)
        before[other.offset - lo : other.end - lo] = other.before[0]
        before[self.offset - lo : self.end - lo] = self.before[0]
        after = bytearray(hi - lo)
        after[self.offset - lo : self.end - lo] = self.after[0]
        after[other.offset - lo : other.end - lo] = other.after[0]
        self.offset = lo
        self.before = (bytes(before), self.before[1])
        self.after = (bytes(after), other.after[1])
        if self.after[0] == self.before[0]:
            self.setObsolete(True)
            self.window.workspace.stamp(
                self.window._bytes_owner(self.entry), self.before[1]
            )
        return True

    def _apply(self, state) -> None:
        data, revision = state
        self.window.apply_strings_edit(self.entry, self.offset, data, revision)


class BytesCommand(_EditContextCommand):
    """A splice of bytes into a file entry's buffer (hex overtype)."""

    def __init__(self, window, entry: Entry, offset: int, before: bytes, after: bytes):
        revision = entry.live_revision
        super().__init__(
            window,
            entry,
            f"Edit bytes at {offset:X}",
            (before, revision),
            (after, window.workspace.next_revision()),
            "raw",
            offset,
        )
        self.offset = offset

    def _apply(self, state) -> None:
        data, revision = state
        self.window.apply_bytes(self.entry, self.offset, data, revision)


class RenameEntryCommand(_InPlaceCommand):
    """An entry's name: the Files panel's inline rename and Rename…."""

    def __init__(self, window, entry: Entry, before: str, after: str):
        super().__init__(window, entry, f"Rename {before}", before, after)

    def _apply(self, state: str) -> None:
        self.window.apply_entry_name(self.entry, state)


class BlockEditCommand(_MergingCommand, _CurrentEntryCommand):
    """A block's name and configuration, changed together in one step.

    The string records travel with it: the window stashes the translations before
    it drops the document, so an edit (and its undo) re-reads the region without
    losing what was typed into it.

    ``field`` names the bar control the change came from, so a run on one — a
    spin box stepped up several times — is one step; ``None`` never merges.
    """

    def __init__(self, window, entry: Entry, before: tuple, after: tuple, field=None):
        super().__init__(window, entry, f"Edit {entry.name}", before, after)
        self.field = field

    def id(self) -> int:
        return BLOCK_ID

    def _mergeable(self, other) -> bool:
        return (
            isinstance(other, BlockEditCommand)
            and self.field is not None
            and other.entry is self.entry
            and other.field == self.field
        )

    def _apply(self, state: tuple) -> None:
        self.window.apply_block_config(self.entry, *state)


class ContainerCommand(_CurrentEntryCommand):
    """A file entry's container and the files joined into it."""

    def __init__(self, window, entry: Entry, before: tuple, after: tuple):
        super().__init__(
            window, entry, f"Edit container of {entry.name}", before, after
        )

    def _apply(self, state: tuple) -> None:
        self.window.apply_container(self.entry, *state)


class EntryOrderCommand(_InPlaceCommand):
    """The order of the whole entry list: a drag, Move Up/Down, or a sort.

    Held as the two full orders rather than as a move, so redo lands exactly what
    the first run did however the rows are grouped on screen.
    """

    def __init__(self, window, text: str, before: list[Entry], after: list[Entry]):
        # No entry of its own: the change is the list, which is why it reaches
        # nothing and why the base class's entry slot holds None.
        super().__init__(window, None, text, before, after)

    def _apply(self, state: list[Entry]) -> None:
        self.window.apply_entry_order(state)


def _changed(before, after) -> frozenset[str]:
    """Which fields of two frozen values differ.

    Either side may be ``None`` — a block gets its first box, or loses it —
    and then every field counts as changed, so such a step never merges with
    an edit of one field.
    """
    if before is None or after is None:
        value = after if before is None else before
        return (
            frozenset(f.name for f in fields(value))
            if value is not None
            else frozenset()
        )
    return frozenset(
        f.name
        for f in fields(before)
        if getattr(before, f.name) != getattr(after, f.name)
    )


class _ValueCommand(_MergingCommand, _InPlaceCommand):
    """One frozen value on an entry, replaced whole, with its revision token.

    Consecutive edits of **the same fields** of the same entry merge, so typing a
    number into one spin box is one step while moving to the next field starts
    another. In place because both of these show in a panel and in the Preview
    window rather than in the view the user is navigating.
    """

    _id = 0
    _stamps = True

    def __init__(self, window, entry: Entry, before, after, text: str):
        revision = entry.live_revision
        super().__init__(
            window,
            entry,
            text,
            (before, revision),
            (after, revision if after == before else window.workspace.next_revision()),
        )

    def id(self) -> int:
        return self._id

    def _mergeable(self, other) -> bool:
        if type(other) is not type(self) or other.entry is not self.entry:
            return False
        return _changed(self.before[0], self.after[0]) == _changed(
            other.before[0], other.after[0]
        )

    def _apply(self, state) -> None:
        raise NotImplementedError


class FontCommand(_ValueCommand):
    """A font entry's ``Font``: sheet geometry, its alphabet and its widths."""

    _id = FONT_ID

    def __init__(self, window, entry: Entry, before, after):
        super().__init__(window, entry, before, after, f"Edit font {entry.name}")

    def _apply(self, state) -> None:
        font, revision = state
        self.window.apply_font(self.entry, font, revision)


class BoxCommand(_ValueCommand):
    """A block's ``TextBox``: its geometry and its codes' layout effects."""

    _id = BOX_ID

    def __init__(self, window, entry: Entry, before, after):
        super().__init__(window, entry, before, after, f"Edit text box of {entry.name}")

    def _apply(self, state) -> None:
        box, revision = state
        self.window.apply_box(self.entry, box, revision)


def _changed_row(before: tuple, after: tuple) -> int | None:
    """The one row the edit is on: the index the two lists differ at, when
    they differ at exactly one, or the last row when it was just appended —
    a term typed into a new row, which its translation then joins. ``None``
    otherwise."""
    if len(after) == len(before) + 1 and before == after[:-1]:
        return len(before)
    if len(before) != len(after):
        return None
    rows = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    return rows[0] if len(rows) == 1 else None


class GlossaryCommand(_MergingCommand, _InPlaceCommand):
    """The project's glossary, replaced whole: the terms before and after.

    Consecutive edits of one row merge — a term typed and then its
    translation is one step — while an added or removed row starts another.
    """

    def __init__(self, window, before: list, after: list):
        super().__init__(window, None, "Edit glossary", tuple(before), tuple(after))

    def id(self) -> int:
        return GLOSSARY_ID

    def _mergeable(self, other) -> bool:
        if not isinstance(other, GlossaryCommand):
            return False
        row = _changed_row(self.before, self.after)
        return row is not None and row == _changed_row(other.before, other.after)

    def _apply(self, state) -> None:
        self.window.apply_glossary(list(state))


class TableCommand(_InPlaceCommand):
    """One Table Editor change: the entry's table before and after it.

    Never merges. One gesture — a line edited, a fill, a shift, a removal — is
    one step, and each re-decodes every view reading that table. In place because
    the Table Editor is where the change shows, and a table is read by entries
    the view is not on.
    """

    def __init__(self, window, entry: Entry, before: Table, after: Table):
        revision = entry.live_revision
        super().__init__(
            window,
            entry,
            f"Edit table {entry.name}",
            (before, revision),
            (after, window.workspace.next_revision()),
        )

    def _apply(self, state) -> None:
        table, revision = state
        self.window.apply_table(self.entry, table, revision)


class PointerCommand(_CurrentEntryCommand):
    """The pointers **Attach** put on a block's strings, as one step.

    One gesture, however many strings the discovery reached: a per-string undo
    would leave the block holding half a result. Carries no revision token,
    because the pointers live on the document rather than in the project file —
    :meth:`~mapchar.ui.main_window.pointers.PointerDiscoveryMixin.apply_pointers`
    says why.
    """

    def __init__(self, window, entry: Entry, before: dict, after: dict):
        super().__init__(
            window, entry, f"Attach pointers in {entry.name}", before, after
        )

    def _apply(self, state) -> None:
        self.window.apply_pointers(self.entry, state)


@dataclass(frozen=True)
class BlockSide:
    """One written compressed block on one side of a write: the payload its
    slot decodes to, and its unsaved state."""

    entry: Entry
    data: bytes
    live: int
    saved: int


@dataclass(frozen=True)
class WriteSide:
    """One file on one side of a write: what its files on disk hold, the buffer
    every entry over it reads, its unsaved state, and the blocks it wrote.

    Each :class:`FileChange` moves the file *towards* this side, so both halves
    of the pair apply the same way and only the pair differs.
    """

    files: tuple[FileChange, ...]
    data: bytes
    live: int
    saved: int
    blocks: tuple[BlockSide, ...]


class WriteCommand(_InPlaceCommand):
    """A write of one file: its bytes on disk, and what sits over them in memory.

    Undoing puts the bytes the write replaced back in the file and in memory,
    so the step reads unsaved as it did before; redoing writes the result once
    more. The file is read at the moment of each
    and only touched while it still holds the side being left — one changed by
    another program since is left alone and the step says so
    (:meth:`~mapchar.pipeline.filechange.FileChange.apply`). In place because a
    write shows in the Files panel's marks wherever the view is, and one Write
    All can cover files the view is not on.
    """

    def __init__(self, window, entry: Entry, before: WriteSide, after: WriteSide):
        super().__init__(window, entry, f"Write {entry.name}", before, after)

    def _apply(self, state: WriteSide) -> None:
        self.window.apply_write(self.entry, state)
