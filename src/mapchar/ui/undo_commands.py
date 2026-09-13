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
