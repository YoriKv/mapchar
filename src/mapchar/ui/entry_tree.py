"""The Files panel's tree widget: reordering by drag, and the keys that act on
entries.

The panel (:mod:`mapchar.ui.files_panel`) fills the rows and acts on what this
reports; the widget itself knows only about items.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

DUPLICATE_KEY = QKeySequence("Ctrl+D")


class EntryTree(QTreeWidget):
    """The Files tree: reorder by drag, and the keys that act on entries.

    A drag never leaves the widget, so the dragged row is read off the tree
    rather than out of the drop's mime data, and the drop only means anything
    between two *siblings*: a row taken *onto* another would be a re-pointing,
    which is a dialog's decision, not an aim's.
    """

    delete_pressed = Signal()
    cut_pressed = Signal()
    copy_pressed = Signal()
    paste_pressed = Signal()
    duplicate_pressed = Signal()
    rename_pressed = Signal()
    move_pressed = Signal(int)
    """Alt+Up / Alt+Down: step the selection one place, ``-1`` or ``+1``."""
    reorder_dropped = Signal(object, object)
    """The dragged row's entry key, and the key it should land in front of."""
    current_navigated = Signal(object)
    """A key moved the current row: the row it moved to, to be shown the same
    way a click on it would show it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._dragged: QTreeWidgetItem | None = None
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
        # A drag moves the one row it started on, so it has nothing to say
        # about a set of them: Move Up/Down is what reorders a selection.
        if len(self.selectedItems()) > 1:
            return
        self._dragged = self.currentItem()
        # Qt accepts a drop *between* two rows only when their parent is a drop
        # target, so the group being rearranged is opened for the length of the
        # drag; the rest of the time nothing here is a drop target at all.
        group = self._dragged.parent() if self._dragged is not None else None
        if group is not None:
            group.setFlags(group.flags() | Qt.ItemFlag.ItemIsDropEnabled)
        try:
            super().startDrag(actions)
        finally:
            if group is not None:
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            self._dragged = None

    def _drop_before(self, event):
        """The dragged item and the sibling it would land in front of, or
        ``None`` when this drop is not a reorder we allow."""
        source = self._dragged
        if source is None:
            return None
        target = self.itemAt(event.position().toPoint())
        if target is None or target is source:
            return None
        parent = source.parent()
        if parent is None or target.parent() is not parent:
            return None
        position = self.dropIndicatorPosition()
        if position is QTreeWidget.DropIndicatorPosition.AboveItem:
            return source, target
        if position is QTreeWidget.DropIndicatorPosition.BelowItem:
            index = parent.indexOfChild(target) + 1
            after = parent.child(index) if index < parent.childCount() else None
            return source, after
        return None

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
        source, before = landing
        # Accepted, but as IgnoreAction and without the base class: Qt's own
        # internal move would rearrange the view behind the workspace's back,
        # leaving an order nothing agreed to and no undo step for it.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.reorder_dropped.emit(
            source.data(0, Qt.ItemDataRole.UserRole),
            before.data(0, Qt.ItemDataRole.UserRole) if before is not None else None,
        )


__all__ = ["DUPLICATE_KEY", "EntryTree"]
