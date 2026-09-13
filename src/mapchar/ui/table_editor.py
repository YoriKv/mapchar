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
from mapchar.core.table import Table
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
        self.save = QPushButton("Save")
        row.addWidget(self.new_line, 1)
        row.addWidget(self.add)
        row.addWidget(self.remove)
        row.addWidget(self.save)
        layout.addLayout(row)
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.table_pick.currentIndexChanged.connect(self._fill)
        self.add.clicked.connect(self._add)
        self.new_line.returnPressed.connect(self._add)
        self.remove.clicked.connect(self._remove)
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
