"""Undo commands: thin before/after pairs that call window ``apply_*`` methods."""

from __future__ import annotations

from PySide6.QtGui import QUndoCommand

from mapchar.project.workspace import Entry

OFFSET_ID = 1


class EntryCommand(QUndoCommand):
    """Add (or remove) an entry together with its children."""

    def __init__(self, window, entry: Entry, add: bool):
        super().__init__(("Add " if add else "Remove ") + entry.name)
        self.window = window
        self.entry = entry
        self.add = add
        self.index: int | None = None
        self.children: list[Entry] = []

    def _do_add(self) -> None:
        self.window.apply_entry_add(self.entry, self.index, self.children)

    def _do_remove(self) -> None:
        ws = self.window.workspace
        self.index = ws.entries.index(self.entry) if self.entry in ws.entries else None
        removed = self.window.apply_entry_remove(self.entry)
        self.children = [e for e in removed if e is not self.entry]

    def redo(self) -> None:
        self._do_add() if self.add else self._do_remove()

    def undo(self) -> None:
        self._do_remove() if self.add else self._do_add()


class OffsetCommand(QUndoCommand):
    """A view move; consecutive moves in one entry merge."""

    def __init__(self, window, entry: Entry, before: int, after: int):
        super().__init__("Move view")
        self.window = window
        self.entry = entry
        self.before = before
        self.after = after
        self._first = True

    def id(self) -> int:
        return OFFSET_ID

    def mergeWith(self, other) -> bool:
        if not isinstance(other, OffsetCommand) or other.entry is not self.entry:
            return False
        self.after = other.after
        if self.after == self.before:
            self.setObsolete(True)
        return True

    def redo(self) -> None:
        if self._first:
            self._first = False
            self._apply(self.after)
            return
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)

    def _apply(self, offset: int) -> None:
        w = self.window
        w._applying_undo = True
        try:
            w.apply_offset(self.entry, offset)
        finally:
            w._applying_undo = False


FIELD_ID = 2


class StringFieldCommand(QUndoCommand):
    """One field of one string: translation, notes or status.

    Consecutive edits of the same field of the same string merge into one
    step, so a run of typing in a cell is undone at once.
    """

    def __init__(self, window, entry: Entry, index: int, field: str, before, after):
        super().__init__(f"Edit {field}")
        self.window = window
        self.entry = entry
        self.index = index
        self.field = field
        self.before = before
        self.after = after

    def id(self) -> int:
        return FIELD_ID

    def mergeWith(self, other) -> bool:
        if (
            not isinstance(other, StringFieldCommand)
            or other.entry is not self.entry
            or other.index != self.index
            or other.field != self.field
        ):
            return False
        self.after = other.after
        return True

    def redo(self) -> None:
        self.window.apply_string_field(self.entry, self.index, self.field, self.after)

    def undo(self) -> None:
        self.window.apply_string_field(self.entry, self.index, self.field, self.before)


class BytesCommand(QUndoCommand):
    """A splice of bytes into a file entry's buffer (hex overtype)."""

    def __init__(self, window, entry: Entry, offset: int, before: bytes, after: bytes):
        super().__init__(f"Edit bytes at {offset:X}")
        self.window = window
        self.entry = entry
        self.offset = offset
        self.before = before
        self.after = after

    def redo(self) -> None:
        self.window.apply_bytes(self.entry, self.offset, self.after)

    def undo(self) -> None:
        self.window.apply_bytes(self.entry, self.offset, self.before)
