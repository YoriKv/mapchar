"""A block's skip ranges: the ranges inside a region that are not text.

The Reading bar shows them as one line and edits them in a popup under it,
rather than in a dropdown of its own.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.numbers import parse_hex
from mapchar.ui.number_fields import HEX_NUMBER
from mapchar.ui.popup_picker import PopupFrame, PopupPicker
from mapchar.ui.widgets import fit_chars


class _HexDelegate(QStyledItemDelegate):
    """Cells edited as hex numbers."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt override
        editor = QLineEdit(parent)
        editor.setValidator(QRegularExpressionValidator(HEX_NUMBER, editor))
        return editor


class SkipsPopup(PopupFrame):
    """The list of a block's skip ranges, edited in a popup under its picker.

    A row is a ``from`` and a ``to`` in hex; the list applies as it changes,
    and a row with a side still blank or unreadable is left out until it can
    be read whole. The popup closes on Esc or a click outside it.
    """

    changed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Reading that reaches From continues at To (hex)"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["From", "To"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setItemDelegate(_HexDelegate(self.table))
        self.table.setMinimumSize(fit_chars(QLineEdit(), 10).minimumWidth() * 2, 120)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(lambda: self.add(None, None))
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_current)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        layout.addLayout(row)

    def set_value(self, skips: tuple[tuple[int, int], ...]) -> None:
        """Show ``skips``; a list already showing them, blank rows and all, is
        left as it is, so a reload after an edit does not lose the row being
        typed into."""
        if self.value() == tuple(skips):
            return
        self._loading = True
        try:
            self.table.setRowCount(0)
            for a, b in skips:
                self._append(f"{a:X}", f"{b:X}")
        finally:
            self._loading = False

    def value(self) -> tuple[tuple[int, int], ...]:
        """The rows that read as a pair of numbers."""
        skips = []
        for row in range(self.table.rowCount()):
            try:
                a = parse_hex(self._text(row, 0), None)
                b = parse_hex(self._text(row, 1), None)
            except ValueError:
                continue
            if a is not None and b is not None:
                skips.append((a, b))
        return tuple(skips)

    def add(self, start: int | None, stop: int | None) -> None:
        """Append a range — blank, to be typed, when either side is ``None``."""
        row = self._append(
            "" if start is None else f"{start:X}", "" if stop is None else f"{stop:X}"
        )
        self.table.setCurrentCell(row, 0)
        if start is None or stop is None:
            self.table.editItem(self.table.item(row, 0 if start is None else 1))
        else:
            self.changed.emit()

    def remove_current(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        had = self.value()
        self.table.removeRow(row)
        if self.value() != had:
            self.changed.emit()

    def _append(self, start: str, stop: str) -> int:
        was, self._loading = self._loading, True
        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(start))
            self.table.setItem(row, 1, QTableWidgetItem(stop))
        finally:
            self._loading = was
        return row

    def _text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def _on_cell_changed(self, row: int, column: int) -> None:
        if not self._loading:
            self.changed.emit()


class SkipsPicker(PopupPicker):
    """A block's skip ranges: the list as one line, opening on the popup that
    edits it in place of a dropdown."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(140, "none", parent)
        self.setToolTip("Byte ranges inside the region that are not text")
        self.popup = SkipsPopup(self)
        self.popup.changed.connect(self._on_changed)

    def set_value(self, skips: tuple[tuple[int, int], ...]) -> None:
        self.popup.set_value(skips)
        self._say()

    def value(self) -> tuple[tuple[int, int], ...]:
        return self.popup.value()

    def add(self, start: int, stop: int) -> None:
        """Append ``start`` to ``stop`` and apply it."""
        self.popup.add(start, stop)

    def _on_changed(self) -> None:
        self._say()
        self.changed.emit()

    def _say(self) -> None:
        self.set_summary(", ".join(f"{a:X}>{b:X}" for a, b in self.value()) or "none")

    def _focus_popup(self) -> None:
        # The list is what the picker is opened to type in.
        self.popup.table.setFocus()


__all__ = ["SkipsPicker", "SkipsPopup"]
