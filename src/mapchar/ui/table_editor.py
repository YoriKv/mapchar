"""The Table Editor: a grid of entries over a table entry, saved as native."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.bits import parse_hex
from mapchar.core.errors import TableError
from mapchar.core.table import Entry as TableEntry
from mapchar.core.table import Table
from mapchar.engines.relsearch import (
    DIGIT,
    HIRAGANA,
    KATAKANA,
    LOWER,
    RUNS,
    UPPER,
    entries_from_base,
)
from mapchar.project.formats.table_native import format_entry, format_key, parse_entry
from mapchar.project.workspace import Entry
from mapchar.ui.widgets import (
    ElidedLabel,
    EscapeCloses,
    fit_chars,
    hint_field,
    show_elided_tooltips,
)
from mapchar.ui.window_layout import remember_layout

ALPHABETS = {
    "A-Z": UPPER,
    "a-z": LOWER,
    "0-9": DIGIT,
    "あ-ん": HIRAGANA,
    "ア-ン": KATAKANA,
}
"""The Fill dialog's canned runs, by the alphabet each names."""


class TableEditor(EscapeCloses, QWidget):
    changed = Signal(object, object)
    """The table entry whose table changed, and its table as it was.

    The window turns the pair into one undo step; the editor itself mutates
    the table in place.
    """
    save_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Table Editor")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "table_editor")
        self._entry: Entry | None = None
        self._table: Table | None = None
        layout = QVBoxLayout(self)
        self.title = ElidedLabel("No table")
        layout.addWidget(self.title)
        self.grid = QTableWidget(0, 2)
        self.grid.setHorizontalHeaderLabels(["Entry line", "Meaning"])
        self.grid.horizontalHeader().setStretchLastSection(True)
        show_elided_tooltips(self.grid)
        layout.addWidget(self.grid, 1)
        row = QHBoxLayout()
        self.new_line = hint_field(
            QLineEdit(),
            "41=A   /FF=[end]   $F0=[color],u8   !F1=[item] @items:1",
            "An entry line in the native grammar, such as\n"
            "41=A   /FF=[end]   $F0=[color],u8   !F1=[item] @items:1\n"
            "Enter or Add puts it in the table",
        )
        fit_chars(self.new_line, 14)
        self.add = QPushButton("Add")
        self.remove = QPushButton("Remove")
        self.add.setToolTip("Add the typed line to the table (Enter)")
        self.remove.setToolTip("Remove the selected entries")
        self.shift = QPushButton("Shift Keys…")
        self.shift.setToolTip("Move the selected entries' keys by a constant")
        self.fill = QPushButton("Fill…")
        self.fill.setToolTip("Lay a run of characters over consecutive keys")
        self.save = QPushButton("Save")
        self.save.setToolTip("Write the table file, in the native grammar")
        row.addWidget(self.new_line, 1)
        row.addWidget(self.add)
        row.addWidget(self.remove)
        row.addWidget(self.shift)
        row.addWidget(self.fill)
        row.addWidget(self.save)
        layout.addLayout(row)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)
        self.add.clicked.connect(self._add)
        self.new_line.returnPressed.connect(self._add)
        self.remove.clicked.connect(self._remove)
        self.shift.clicked.connect(self._shift)
        self.fill.clicked.connect(self._fill_dialog)
        self.save.clicked.connect(lambda: self.save_requested.emit(self._entry))
        self.grid.itemChanged.connect(self._edited)
        self._filling = False
        self.resize(640, 480)

    def set_entry(self, entry: Entry | None) -> None:
        self._entry = entry
        if entry is None or entry.table is None:
            self.title.setText(entry.name if entry is not None else "No table")
        else:
            self.title.setText(f"@{entry.table.id} · {entry.name}")
        self._fill()

    def prefill(self, key_bits: str) -> None:
        self.new_line.setText(f"{format_key(key_bits)}=")
        self.new_line.setFocus()

    def _snapshot(self) -> Table | None:
        """The entry's table as it is now, to undo back to."""
        return deepcopy(self._table)

    def _fill(self) -> None:
        self._filling = True
        self._table = self._entry.table if self._entry is not None else None
        self.grid.setRowCount(0)
        if self._table is not None:
            entries = self._table.sorted_entries()
            self.grid.setRowCount(len(entries))
            for row, e in enumerate(entries):
                line = QTableWidgetItem(format_entry(e))
                line.setData(Qt.ItemDataRole.UserRole, e.bits)
                meaning = QTableWidgetItem(self._meaning(e))
                meaning.setFlags(meaning.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.grid.setItem(row, 0, line)
                self.grid.setItem(row, 1, meaning)
        self.grid.resizeColumnToContents(0)
        self._filling = False

    @staticmethod
    def _meaning(e: TableEntry) -> str:
        kind = e.kind.value
        if e.operands:
            return f"{kind}: {len(e.operands)} operand(s)"
        if e.params:
            return f"{kind}: " + " ".join(p.spec() for p in e.params)
        return kind

    def _apply_line(self, line: str, replace_bits: str | None) -> bool:
        table = self._table
        if table is None:
            return False
        try:
            entry = parse_entry(line.strip())
        except ValueError as exc:
            self.status.setText(str(exc))
            return False
        before = self._snapshot()
        try:
            if replace_bits is not None and replace_bits != entry.bits:
                table.remove(replace_bits)
            table.add(entry, replace=True)
        except TableError as exc:
            self.status.setText(exc.message)
            return False
        self.status.setText("")
        self.changed.emit(self._entry, before)
        return True

    def _add(self) -> None:
        if self._apply_line(self.new_line.text(), None):
            self.new_line.clear()
            self._fill()

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 0:
            return
        bits = item.data(Qt.ItemDataRole.UserRole)
        if self._apply_line(item.text(), bits):
            self._fill()
        else:
            self._fill()

    def _shift(self) -> None:
        """Move the selected entries' keys by a constant (a hex delta)."""
        from PySide6.QtWidgets import QInputDialog

        table = self._table
        rows = sorted({i.row() for i in self.grid.selectedItems()})
        if table is None or not rows:
            self.status.setText("Select the entries to shift.")
            return
        text, ok = QInputDialog.getText(
            self, "Shift Keys", "Add to each key (hex, may be negative):"
        )
        if not ok or not text.strip():
            return
        try:
            delta = parse_hex(text)
        except ValueError:
            self.status.setText("Not a hex number.")
            return
        entries = [
            table.entries[self.grid.item(r, 0).data(Qt.ItemDataRole.UserRole)]
            for r in rows
        ]
        before = self._snapshot()
        moved = []
        for e in entries:
            value = int(e.bits, 2) + delta
            if value < 0 or value >= 1 << len(e.bits):
                self.status.setText(f"{format_key(e.bits)} would leave its width.")
                return
            moved.append((e, format(value, f"0{len(e.bits)}b")))
        for e, _ in moved:
            table.remove(e.bits)
        try:
            for e, bits in moved:
                table.add(
                    TableEntry(bits, e.kind, e.text, e.weight, e.operands, e.params)
                )
        except TableError as exc:
            self.status.setText(exc.message)
        self.changed.emit(self._entry, before)
        self._fill()

    def _fill_dialog(self) -> None:
        """Lay a string of characters over consecutive keys from a start key."""
        from PySide6.QtWidgets import QInputDialog

        table = self._table
        if table is None:
            return
        templates = [*ALPHABETS, "A-Z a-z 0-9", "Custom…"]
        choice, ok = QInputDialog.getItem(
            self, "Fill", "Characters:", templates, 0, False
        )
        if not ok:
            return
        if choice == "Custom…":
            chars, ok = QInputDialog.getText(self, "Fill", "Characters in key order:")
            if not ok or not chars:
                return
        else:
            chars = "".join(RUNS[ALPHABETS[part]] for part in choice.split())
        start_text, ok = QInputDialog.getText(
            self, "Fill", "First key (hex):", text="00"
        )
        if not ok:
            return
        digits = start_text.strip().replace("$", "")
        try:
            start = parse_hex(digits, default=-1)
        except ValueError:
            start = -1
        if start < 0:
            self.status.setText("Not a hex key.")
            return
        width = max(len(digits), 2)
        entries = entries_from_base(start, width * 4, "big", chars)
        taken = [e for e in entries if e.bits in table.entries]
        overwrite = False
        if taken:
            answer = QMessageBox.question(
                self,
                "Fill",
                f"{len(taken)} of these keys already have entries "
                f"(from {format_key(taken[0].bits)}). Overwrite them?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            overwrite = answer == QMessageBox.StandardButton.Yes
        before = self._snapshot()
        added = 0
        for entry in entries:
            if entry.bits in table.entries and not overwrite:
                continue
            table.add(entry, replace=True)
            added += 1
        kept = len(entries) - added
        self.status.setText(
            f"Filled {added} entries from {start:0{width}X}."
            + (f" {kept} key(s) already taken were left alone." if kept else "")
        )
        self.changed.emit(self._entry, before)
        self._fill()

    def _remove(self) -> None:
        table = self._table
        rows = sorted({i.row() for i in self.grid.selectedItems()}, reverse=True)
        if table is None or not rows:
            return
        if (
            QMessageBox.question(
                self,
                "Remove Entries",
                f"Remove {len(rows)} {'entry' if len(rows) == 1 else 'entries'}?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        before = self._snapshot()
        for row in rows:
            bits = self.grid.item(row, 0).data(Qt.ItemDataRole.UserRole)
            table.remove(bits)
        self.changed.emit(self._entry, before)
        self._fill()
