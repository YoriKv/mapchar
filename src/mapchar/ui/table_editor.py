"""The Table Editor: a grid of entries over a table entry, saved as native."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.errors import TableError
from mapchar.core.table import Entry as TableEntry
from mapchar.core.table import EntryKind, Table
from mapchar.project.formats.table_native import format_entry, format_key, parse_entry
from mapchar.project.workspace import Entry


class TableEditor(QWidget):
    changed = Signal(object)
    """The table entry whose tables changed."""
    save_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Table Editor")
        self._entry: Entry | None = None
        self._table: Table | None = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = QLabel("No table")
        self.table_pick = QComboBox()
        top.addWidget(self.title, 1)
        top.addWidget(QLabel("Table"))
        top.addWidget(self.table_pick)
        layout.addLayout(top)
        self.grid = QTableWidget(0, 2)
        self.grid.setHorizontalHeaderLabels(["Entry line", "Meaning"])
        self.grid.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.grid, 1)
        row = QHBoxLayout()
        self.new_line = QLineEdit()
        self.new_line.setPlaceholderText(
            "41=A   /FF=[end]   $F0=[color],u8   !F1=[item] @items:1"
        )
        self.add = QPushButton("Add")
        self.remove = QPushButton("Remove")
        self.shift = QPushButton("Shift keys…")
        self.fill = QPushButton("Fill…")
        self.save = QPushButton("Save")
        row.addWidget(self.new_line, 1)
        row.addWidget(self.add)
        row.addWidget(self.remove)
        row.addWidget(self.shift)
        row.addWidget(self.fill)
        row.addWidget(self.save)
        layout.addLayout(row)
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.table_pick.currentIndexChanged.connect(self._fill)
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
        self.table_pick.blockSignals(True)
        self.table_pick.clear()
        if entry is not None:
            self.title.setText(entry.name)
            for t in entry.tables:
                self.table_pick.addItem(t.id, t.id)
        else:
            self.title.setText("No table")
        self.table_pick.blockSignals(False)
        self._fill()

    def select_table(self, table_id: str) -> None:
        i = self.table_pick.findData(table_id)
        if i >= 0:
            self.table_pick.setCurrentIndex(i)

    def prefill(self, key_bits: str) -> None:
        self.new_line.setText(f"{format_key(key_bits)}=")
        self.new_line.setFocus()

    def _current_table(self) -> Table | None:
        if self._entry is None:
            return None
        tid = self.table_pick.currentData()
        for t in self._entry.tables:
            if t.id == tid:
                return t
        return None

    def _fill(self) -> None:
        self._filling = True
        self._table = self._current_table()
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
        try:
            if replace_bits is not None and replace_bits != entry.bits:
                table.remove(replace_bits)
            table.add(entry, replace=True)
        except TableError as exc:
            self.status.setText(exc.message)
            return False
        self.status.setText("")
        self.changed.emit(self._entry)
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
            self, "Shift keys", "Add to each key (hex, may be negative):"
        )
        if not ok or not text.strip():
            return
        try:
            delta = int(text.strip().replace("$", ""), 16)
        except ValueError:
            self.status.setText("Not a hex number.")
            return
        entries = [
            table.entries[self.grid.item(r, 0).data(Qt.ItemDataRole.UserRole)]
            for r in rows
        ]
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
        self.changed.emit(self._entry)
        self._fill()

    def _fill_dialog(self) -> None:
        """Lay a string of characters over consecutive keys from a start key."""
        from PySide6.QtWidgets import QInputDialog

        table = self._table
        if table is None:
            return
        templates = ["A-Z", "a-z", "0-9", "A-Z a-z 0-9", "custom…"]
        choice, ok = QInputDialog.getItem(
            self, "Fill", "Characters:", templates, 0, False
        )
        if not ok:
            return
        if choice == "custom…":
            chars, ok = QInputDialog.getText(self, "Fill", "Characters in key order:")
            if not ok or not chars:
                return
        else:
            chars = "".join(
                {
                    "A-Z": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    "a-z": "abcdefghijklmnopqrstuvwxyz",
                    "0-9": "0123456789",
                }[part]
                for part in choice.split()
            )
        start_text, ok = QInputDialog.getText(
            self, "Fill", "First key (hex):", text="00"
        )
        if not ok:
            return
        try:
            width = max(len(start_text.strip().replace("$", "")), 2)
            start = int(start_text.strip().replace("$", ""), 16)
        except ValueError:
            self.status.setText("Not a hex key.")
            return
        bits_width = width * 4
        added = 0
        for i, ch in enumerate(chars):
            value = start + i
            if value >= 1 << bits_width:
                break
            bits = format(value, f"0{bits_width}b")
            from mapchar.core.tokens import escape_text

            table.add(TableEntry(bits, EntryKind.TEXT, escape_text(ch)), replace=True)
            added += 1
        self.status.setText(f"Filled {added} entries from {start:0{width}X}.")
        self.changed.emit(self._entry)
        self._fill()

    def _remove(self) -> None:
        table = self._table
        rows = sorted({i.row() for i in self.grid.selectedItems()}, reverse=True)
        if table is None or not rows:
            return
        if (
            QMessageBox.question(self, "Remove", f"Remove {len(rows)} entr(ies)?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        for row in rows:
            bits = self.grid.item(row, 0).data(Qt.ItemDataRole.UserRole)
            table.remove(bits)
        self.changed.emit(self._entry)
        self._fill()
