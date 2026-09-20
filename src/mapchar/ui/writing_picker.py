"""A block's write settings: how a write lays its strings out.

Set once per block and then left alone, so the Reading bar shows them as one
line — the mode, where the room ends, the fill — and edits them in a popup
under it, as it does the skip ranges.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFormLayout, QFrame, QWidget

from mapchar.ui.widgets import CompactComboBox


class WritingPopup(QFrame):
    """The write settings' fields, in a popup under their picker. They apply as
    they are edited; the popup closes on Esc or a click outside it."""

    def __init__(self, parent: QWidget, rows: tuple[tuple[str, QWidget], ...]):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        form = QFormLayout(self)
        form.setContentsMargins(8, 8, 8, 8)
        for label, field in rows:
            form.addRow(label, field)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)


class WritingPicker(CompactComboBox):
    """The write settings as one line, opening on the popup that edits them in
    place of a dropdown."""

    def __init__(
        self, rows: tuple[tuple[str, QWidget], ...], parent: QWidget | None = None
    ):
        super().__init__(250, parent)
        self.addItem("")
        self.setToolTip("How a write lays the strings out, and up to where")
        self.popup = WritingPopup(self, rows)

    def set_summary(self, text: str) -> None:
        self.setItemText(0, text)

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        below = self.mapToGlobal(QPoint(0, self.height()))
        screen = self.screen().availableGeometry()
        size = self.popup.sizeHint()
        x = min(below.x(), screen.right() - size.width())
        y = below.y()
        if y + size.height() > screen.bottom():
            y = self.mapToGlobal(QPoint(0, 0)).y() - size.height()
        self.popup.move(max(x, screen.left()), max(y, screen.top()))
        self.popup.show()

    def hidePopup(self) -> None:  # noqa: N802 - Qt override
        pass


__all__ = ["WritingPicker", "WritingPopup"]
