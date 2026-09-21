"""A pointer list's addresses: where each pointer to a string sits.

A list has no length a bar can hold, so the Reading bar shows it as one line
and edits it in a popup under that line, the way it shows a block's skip
ranges.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Signal
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

from mapchar.core.address import AddressLayout
from mapchar.ui.number_fields import (
    ADDRESS_CHARS,
    ADDRESS_NUMBER,
    AddressSpelling,
    respell_addresses,
)
from mapchar.ui.popup_picker import PopupFrame, PopupPicker
from mapchar.ui.widgets import fit_chars

PICKER_CHARS = 24
"""How wide the one line is: the addresses a short list holds, since a pointer
list's line is longer than a picker's usual item."""

_ADDRESS_LIST = QRegularExpression(
    f"{ADDRESS_NUMBER.pattern()}(,{ADDRESS_NUMBER.pattern()})*"
)
"""What a cell accepts while it is typed: one address, or several separated by
commas, so a list written down elsewhere still goes in at once."""


class _AddressDelegate(QStyledItemDelegate):
    """Cells typed as an address, or as a comma-separated run of them."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt override
        editor = QLineEdit(parent)
        editor.setValidator(QRegularExpressionValidator(_ADDRESS_LIST, editor))
        return editor


class AddressesPopup(PopupFrame):
    """The addresses a pointer list reads, edited in a popup under its picker.

    A row is one address, spelled the way every address field spells one and
    re-spelled when the address format changes. The list applies as it changes,
    and a row still blank or unreadable is left out until it can be read, so
    the row being typed into does not take the others with it. A row given
    several addresses separated by commas spreads over a row each. The popup
    closes on Esc or a click outside it.
    """

    changed = Signal()

    def __init__(self, spelling: AddressSpelling, parent: QWidget):
        super().__init__(parent)
        self.spelling = spelling
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Address of each string pointer"))
        self.table = QTableWidget(0, 1)
        self.table.setHorizontalHeaderLabels(["Address"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setItemDelegate(_AddressDelegate(self.table))
        self.table.setMinimumSize(
            fit_chars(QLineEdit(), ADDRESS_CHARS).minimumWidth(), 120
        )
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(lambda: self.add(None))
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_current)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.spelling.changed.connect(self._respell)

    def set_value(self, addresses: tuple[int, ...]) -> None:
        """Show ``addresses``; a list already showing them, blank rows and all,
        is left as it is, so a reload after an edit does not lose the row being
        typed into."""
        if self.value() == tuple(addresses):
            return
        self._loading = True
        try:
            self.table.setRowCount(0)
            for address in addresses:
                self._append(self.spelling.format(address))
        finally:
            self._loading = False

    def value(self) -> tuple[int, ...]:
        """The rows that read as an address, in the order they are listed in —
        which is the order the strings they reach come out in."""
        read = (self.spelling.parse(self._text(row)) for row in range(self._rows()))
        return tuple(address for address in read if address is not None)

    def add(self, address: int | None) -> None:
        """Append an address — blank, to be typed, when ``None``."""
        row = self._append("" if address is None else self.spelling.format(address))
        self.table.setCurrentCell(row, 0)
        if address is None:
            self.table.editItem(self.table.item(row, 0))
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

    def _rows(self) -> int:
        return self.table.rowCount()

    def _append(self, address: str) -> int:
        was, self._loading = self._loading, True
        try:
            row = self._rows()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(address))
        finally:
            self._loading = was
        return row

    def _text(self, row: int) -> str:
        item = self.table.item(row, 0)
        return item.text() if item is not None else ""

    def _on_cell_changed(self, row: int, column: int) -> None:
        if self._loading:
            return
        # Spreading a list over its rows changes them, and says so itself.
        if not self._spread(row):
            self.changed.emit()

    def _spread(self, row: int) -> bool:
        """Lay a row holding a comma-separated list over a row each; whether
        the row held one."""
        text = self._text(row)
        if "," not in text:
            return False
        parts = [part.strip() for part in text.split(",") if part.strip()] or [""]
        was, self._loading = self._loading, True
        try:
            self.table.item(row, 0).setText(parts[0])
            for at, part in enumerate(parts[1:], row + 1):
                self.table.insertRow(at)
                self.table.setItem(at, 0, QTableWidgetItem(part))
        finally:
            self._loading = was
        self.changed.emit()
        return True

    def _respell(self, old: AddressLayout | None) -> None:
        """Spell every row under the format the spelling changed to; a row
        neither format reads is left as it was typed."""
        was, self._loading = self._loading, True
        try:
            for row in range(self._rows()):
                item = self.table.item(row, 0)
                if item is not None:
                    item.setText(respell_addresses(item.text(), self.spelling, old))
        finally:
            self._loading = was


class AddressesPicker(PopupPicker):
    """A pointer list's addresses: the list as one line, opening on the popup
    that edits it in place of a dropdown."""

    changed = Signal()

    def __init__(
        self, spelling: AddressSpelling | None = None, parent: QWidget | None = None
    ):
        super().__init__(
            fit_chars(QLineEdit(), PICKER_CHARS).minimumWidth(), "none", parent
        )
        self.setToolTip("Address of each string pointer")
        self.spelling = spelling if spelling is not None else AddressSpelling(self)
        self.popup = AddressesPopup(self.spelling, self)
        self.popup.changed.connect(self._on_changed)
        # After the popup's own, so the line is written from re-spelled rows.
        self.spelling.changed.connect(lambda _old=None: self._say())

    def set_value(self, addresses: tuple[int, ...]) -> None:
        self.popup.set_value(addresses)
        self._say()

    def value(self) -> tuple[int, ...]:
        return self.popup.value()

    def add(self, address: int) -> None:
        """Append ``address`` and apply it."""
        self.popup.add(address)

    def _on_changed(self) -> None:
        self._say()
        self.changed.emit()

    def _say(self) -> None:
        spelled = ", ".join(self.spelling.format(a) for a in self.value())
        self.set_summary(spelled or "none")

    def _focus_popup(self) -> None:
        # The list is what the picker is opened to type in.
        self.popup.table.setFocus()


__all__ = ["AddressesPicker", "AddressesPopup"]
