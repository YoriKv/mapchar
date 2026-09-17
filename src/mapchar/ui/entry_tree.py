"""The Files panel's tree widget: reordering by drag, and the keys that act on
entries.

The panel (:mod:`mapchar.ui.files_panel`) fills the rows and acts on what this
reports; the widget itself knows only about items.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)
from shiboken6 import isValid

DUPLICATE_KEY = QKeySequence("Ctrl+D")


class EntryTree(QTreeWidget):
    """The Files tree: reorder and regroup by drag, and the keys that act on
    entries.

    A drag never leaves the widget, so the dragged rows are read off the tree
    rather than out of the drop's mime data. A drop lands *between* two rows,
    or *onto* one to go last under it; which of those mean anything is the
    panel's to say (:attr:`drop_allowed`) — a row taken onto a block would be a
    re-pointing, which is a dialog's decision, not an aim's.
    """

    delete_pressed = Signal()
    cut_pressed = Signal()
    copy_pressed = Signal()
    paste_pressed = Signal()
    duplicate_pressed = Signal()
    rename_pressed = Signal()
    move_pressed = Signal(int)
    """Alt+Up / Alt+Down: step the selection one place, ``-1`` or ``+1``."""
    dropped = Signal(list, object, object)
    """The dragged rows' entry keys, the key of the row they land under
    (``None`` for a group heading), and the key they land in front of
    (``None``: last there)."""
    current_navigated = Signal(object)
    """A key moved the current row: the row it moved to, to be shown the same
    way a click on it would show it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._dragged: list[QTreeWidgetItem] = []
        self.drop_allowed: (
            Callable[
                [list[QTreeWidgetItem], QTreeWidgetItem, QTreeWidgetItem | None], bool
            ]
            | None
        ) = None
        """Whether the dragged rows may land under a row, in front of another:
        the panel's answer, since only it knows what the rows stand for."""
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def keyPressEvent(self, event) -> None:
        for sequence, signal in (
            (QKeySequence.StandardKey.Delete, self.delete_pressed),
            (QKeySequence.StandardKey.Cut, self.cut_pressed),
            (QKeySequence.StandardKey.Copy, self.copy_pressed),
            (QKeySequence.StandardKey.Paste, self.paste_pressed),
        ):
            if event.matches(sequence):
                signal.emit()
                event.accept()
                return
        # Compared as a sequence: Qt has no standard key for Duplicate.
        if QKeySequence(event.keyCombination()) == DUPLICATE_KEY:
            self.duplicate_pressed.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_F2 and not event.modifiers():
            self.rename_pressed.emit()
            event.accept()
            return
        if event.modifiers() == Qt.KeyboardModifier.AltModifier and event.key() in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            self.move_pressed.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
            event.accept()
            return
        before = self.currentItem()
        super().keyPressEvent(event)
        item = self.currentItem()
        if item is not None and item is not before:
            self.current_navigated.emit(item)

    def startDrag(self, actions) -> None:  # noqa: N802 - Qt override
        # The selection moves when the drag starts inside it, and the row under
        # the mouse alone otherwise; rows that are no entry — a block's
        # strings — never move.
        current = self.currentItem()
        selected = self.selectedItems()
        if any(current is s for s in selected):
            rows = selected
        else:
            rows = [current] if current is not None else []
        self._dragged = [
            r for r in rows if r.data(0, Qt.ItemDataRole.UserRole) is not None
        ]
        if not self._dragged:
            return
        # Qt accepts a drop *between* two rows only when their parent is a drop
        # target, so the groups being rearranged are opened for the length of
        # the drag; the rest of the time no group heading is a drop target.
        groups = []
        for row in self._dragged:
            group = row.parent()
            if group is not None and not group.flags() & Qt.ItemFlag.ItemIsDropEnabled:
                group.setFlags(group.flags() | Qt.ItemFlag.ItemIsDropEnabled)
                groups.append(group)
        try:
            super().startDrag(actions)
        finally:
            for group in groups:
                # A drop that landed rebuilt the tree, and took the group with it.
                if isValid(group):
                    group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            self._dragged = []

    def landing(
        self, target: QTreeWidgetItem | None, position
    ) -> tuple[QTreeWidgetItem, QTreeWidgetItem | None] | None:
        """The row the dragged rows would land under and the row in front of
        which, for a drop at ``position`` on ``target``; ``None`` when that
        drop is refused."""
        sources = self._dragged
        if not sources or target is None:
            return None
        drop = QTreeWidget.DropIndicatorPosition
        if position is drop.OnItem:
            parent, before = target, None
        elif position is drop.AboveItem:
            parent, before = target.parent(), target
        elif position is drop.BelowItem:
            parent = target.parent()
            index = parent.indexOfChild(target) + 1 if parent is not None else 0
            before = (
                parent.child(index)
                if parent is not None and index < parent.childCount()
                else None
            )
        else:
            return None
        if parent is None or any(parent is s for s in sources):
            return None
        if self.drop_allowed is not None and not self.drop_allowed(
            sources, parent, before
        ):
            return None
        return parent, before

    def _drop_before(self, event):
        return self.landing(
            self.itemAt(event.position().toPoint()), self.dropIndicatorPosition()
        )

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        # The base class decides where the indicator is drawn, so it runs
        # first; what it accepted is then overruled for anything refused above,
        # which is what makes an illegal target show the "no drop" cursor.
        super().dragMoveEvent(event)
        if self._drop_before(event) is None:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        landing = self._drop_before(event)
        if landing is None:
            event.ignore()
            return
        # Accepted, but as IgnoreAction and without the base class: Qt's own
        # internal move would rearrange the view behind the workspace's back,
        # leaving an order nothing agreed to and no undo step for it.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.drop_rows(self._dragged, *landing)

    def drop_rows(
        self,
        rows: list[QTreeWidgetItem],
        parent: QTreeWidgetItem,
        before: QTreeWidgetItem | None,
    ) -> None:
        """Report ``rows`` dropped under ``parent`` in front of ``before``."""
        role = Qt.ItemDataRole.UserRole
        self.dropped.emit(
            [r.data(0, role) for r in rows],
            parent.data(0, role),
            before.data(0, role) if before is not None else None,
        )


__all__ = ["DUPLICATE_KEY", "EntryTree"]
