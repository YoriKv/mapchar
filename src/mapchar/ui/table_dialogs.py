"""The Table Editor's two prompts: how far to shift the selected keys, and
what run of characters to fill consecutive keys with."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QWidget,
)

from mapchar.core.bits import format_key
from mapchar.core.table import Table
from mapchar.engines.relsearch import entries_from_base
from mapchar.ui.alphabets import ALPHABETS, CUSTOM
from mapchar.ui.number_fields import HexEdit, OffsetEdit
from mapchar.ui.widgets import ElidedLabel, hint_field, ok_cancel


class ShiftKeysDialog(QDialog):
    """How far to move the selected keys."""

    def __init__(self, count: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Shift Keys")
        form = QFormLayout(self)
        self.offset = OffsetEdit()
        self.offset.setToolTip(
            "Hex, added to every selected key; a leading − subtracts"
        )
        form.addRow(f"Add to each of the {count} selected key(s)", self.offset)
        form.addRow(ok_cancel(self))
        self.offset.setFocus()

    def delta(self) -> int | None:
        return self.offset.value()


class FillDialog(QDialog):
    """A run of characters over consecutive keys, with a look at what it
    would touch."""

    def __init__(self, table: Table, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Fill")
        self._table = table
        form = QFormLayout(self)
        self.template = QComboBox()
        for name in (*ALPHABETS, CUSTOM):
            self.template.addItem(name, name)
        self.template.setToolTip("The characters, in key order")
        form.addRow("Characters", self.template)
        self.custom = hint_field(
            QLineEdit(),
            "typed in key order",
            "The characters, one per key, in key order",
        )
        form.addRow("", self.custom)
        # The digit count is the key width, so what is typed keeps its own
        # rather than being padded to the field's.
        self.first = HexEdit(6, pad=False)
        hint_field(
            self.first, "00", "The first key, in hex; its digits set every key's width"
        )
        self.first.setText("00")
        form.addRow("First key", self.first)
        self.overwrite = QCheckBox("Overwrite keys that already have entries")
        form.addRow("", self.overwrite)
        self.preview = ElidedLabel("")
        form.addRow(self.preview)
        form.addRow(ok_cancel(self))
        self.template.currentIndexChanged.connect(self._refresh)
        self.custom.textChanged.connect(self._refresh)
        self.first.textChanged.connect(self._refresh)
        self._refresh()

    def chars(self) -> str:
        choice = self.template.currentData()
        if choice == CUSTOM:
            return self.custom.text()
        return ALPHABETS[choice]

    def start(self) -> int | None:
        return self.first.value()

    def width(self) -> int:
        """How many hex digits a key takes: as many as were typed, at least two."""
        typed = self.first.text().strip().replace("$", "").replace("_", "")
        return max(len(typed), 2)

    def _refresh(self) -> None:
        self.custom.setVisible(self.template.currentData() == CUSTOM)
        chars, start = self.chars(), self.start()
        if not chars or start is None:
            self.preview.setText("")
            return
        entries = entries_from_base(start, self.width() * 4, "big", chars)
        taken = sum(e.bits in self._table.entries for e in entries)
        last = format_key(entries[-1].bits) if entries else ""
        self.preview.setText(
            f"{len(entries)} keys, {format_key(entries[0].bits)} to {last}"
            + (f"; {taken} already have entries" if taken else "")
        )


__all__ = ["FillDialog", "ShiftKeysDialog"]
