"""The Search window: find bytes or text, and relative search."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.engines.relsearch import Hit, relative_search


class SearchWindow(QWidget):
    go_to = Signal(int, int)
    """Offset and length to select in the raw view."""
    build_table = Signal(object)
    """A Hit to seed a table from."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Search")
        self._data: bytes = b""
        self._hits: list[Hit] = []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Relative search: letters, digits, ? wildcard")
        self.width = QComboBox()
        self.width.addItem("8-bit", (1,))
        self.width.addItem("16-bit", (2,))
        self.width.addItem("8 and 16-bit", (1, 2))
        self.case_gap = QCheckBox("Case gap")
        self.case_gap.setChecked(True)
        self.case_gap.setToolTip("Upper and lower case may sit at any distance apart")
        self.run = QPushButton("Search")
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        row.addWidget(self.query, 1)
        row.addWidget(self.width)
        row.addWidget(self.case_gap)
        row.addWidget(self.run)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(["Offset", "Width", "Bytes", "Bases"])
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.results, 1)
        bottom = QHBoxLayout()
        self.build = QPushButton("Build table from hit")
        self.build.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.build)
        layout.addLayout(bottom)
        self._cancel = False
        self.run.clicked.connect(self._search)
        self.query.returnPressed.connect(self._search)
        self.stop.clicked.connect(self._on_stop)
        self.results.itemSelectionChanged.connect(self._on_select)
        self.results.itemDoubleClicked.connect(lambda item: self._jump())
        self.build.clicked.connect(self._build)
        self.resize(640, 400)

    def set_data(self, data: bytes) -> None:
        self._data = data

    def _on_stop(self) -> None:
        self._cancel = True

    def _progress(self, done: int, total: int) -> bool:
        self.status.setText(f"Searching… {done * 100 // max(total, 1)}%")
        QApplication.processEvents()
        return not self._cancel

    def _search(self) -> None:
        query = self.query.text()
        if not query or not self._data:
            self.status.setText("Open a ROM and type a query.")
            return
        self._cancel = False
        self.run.setEnabled(False)
        self.stop.setEnabled(True)
        try:
            self._hits = relative_search(
                self._data,
                query,
                widths=self.width.currentData(),
                case_gap=self.case_gap.isChecked(),
                progress=self._progress,
            )
        except ValueError as exc:
            self._hits = []
            self.status.setText(str(exc))
        else:
            self.status.setText(
                f"{len(self._hits)} hit(s)" + (" (stopped)" if self._cancel else "")
            )
        finally:
            self.run.setEnabled(True)
            self.stop.setEnabled(False)
        self._fill()

    def _fill(self) -> None:
        self.results.setRowCount(len(self._hits))
        for row, hit in enumerate(self._hits):
            raw = self._data[hit.offset : hit.offset + hit.length]
            cells = [
                f"{hit.offset:X}",
                f"{hit.width * 8}-bit {hit.endian}" if hit.width > 1 else "8-bit",
                " ".join(f"{b:02X}" for b in raw),
                ", ".join(f"{k}={v:X}" for k, v in hit.bases.items()),
            ]
            for col, text in enumerate(cells):
                self.results.setItem(row, col, QTableWidgetItem(text))
        self.results.resizeColumnsToContents()

    def _current_hit(self) -> Hit | None:
        rows = self.results.selectionModel().selectedRows()
        if not rows:
            return None
        return self._hits[rows[0].row()]

    def _on_select(self) -> None:
        self.build.setEnabled(self._current_hit() is not None)
        self._jump()

    def _jump(self) -> None:
        hit = self._current_hit()
        if hit is not None:
            self.go_to.emit(hit.offset, hit.length)

    def _build(self) -> None:
        hit = self._current_hit()
        if hit is not None:
            self.build_table.emit(hit)
