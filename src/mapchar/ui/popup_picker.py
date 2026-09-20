"""A setting made once, folded away into one line of a bar.

A block's skip ranges and its write settings are set once and then left alone,
so neither earns a row of the Reading bar: each shows as a summary line in a
combo whose dropdown is a panel of fields rather than a list of choices. This
is what the two have in common — where the panel opens, that the combo has no
list to hide, and the one row the summary is written to.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QWidget

from mapchar.ui.widgets import CompactComboBox


class PopupFrame(QFrame):
    """The panel a :class:`PopupPicker` opens: a frame that closes on Esc, or
    on a click outside it, as a dropdown does."""

    def __init__(self, parent: QWidget):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)


class PopupPicker(CompactComboBox):
    """A one-row combo whose row is a summary line and whose dropdown is a
    panel of fields.

    A subclass builds the panel into :attr:`popup` and keeps the row up to date
    with :meth:`set_summary`. The panel opens under the combo, moved back onto
    the screen when it would fall off the side and flipped above the combo when
    it would fall off the bottom.
    """

    popup: QWidget
    """The panel the picker opens; the subclass builds it."""

    def __init__(
        self, width: int, summary: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(width, parent)
        self.addItem(summary)

    def set_summary(self, text: str) -> None:
        """Say in the one row what the panel's fields hold."""
        self.setItemText(0, text)

    def _focus_popup(self) -> None:
        """What takes the focus once the panel is up; nothing by default."""

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
        self._focus_popup()

    def hidePopup(self) -> None:  # noqa: N802 - Qt override
        # There is no list to hide, and the panel closes itself.
        pass


__all__ = ["PopupFrame", "PopupPicker"]
