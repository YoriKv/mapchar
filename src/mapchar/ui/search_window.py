"""The Search window: find bytes or text, and relative search."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mapchar.engines.relsearch import Hit, relative_search
from mapchar.ui.widgets import (
    CancellableRun,
    ElidedLabel,
    EscapeCloses,
    ResultsTable,
    fit_chars,
    hint_field,
)
from mapchar.ui.window_layout import remember_layout


class SearchWindow(EscapeCloses, CancellableRun, QWidget):
    go_to = Signal(int, int)
    """Offset and length to select in the raw view."""
    build_table = Signal(object)
    """A Hit to seed a table from."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Search")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "search_window")
        self._data: bytes = b""
        self._hits: list[Hit] = []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = hint_field(
            QLineEdit(),
            "letters, digits, kana, ? wildcard",
            "A relative search: the letters, digits or kana of a word the game\n"
            "shows, with ? for any character; Enter searches",
        )
        fit_chars(self.query, 16)
        self.width = QComboBox()
        self.width.addItem("8-bit", (1,))
        self.width.addItem("16-bit", (2,))
        self.width.addItem("8 and 16-bit", (1, 2))
        self.case_gap = QCheckBox("Case gap")
        self.case_gap.setChecked(True)
        self.case_gap.setToolTip("Upper and lower case may sit at any distance apart")
        self.limit = QSpinBox()
        self.limit.setRange(1, 1000000)
        self.limit.setValue(500)
        self.limit.setSingleStep(500)
        self.limit.setToolTip("How many hits to keep before stopping")
        self.run = QPushButton("Search")
        self.stop = QPushButton("Stop")
        row.addWidget(self.query, 1)
        row.addWidget(self.width)
        row.addWidget(self.case_gap)
        row.addWidget(QLabel("Limit"))
        row.addWidget(self.limit)
        row.addWidget(self.run)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)
        self.results = ResultsTable(["Offset", "Width", "Bytes", "Bases"])
        layout.addWidget(self.results, 1)
        bottom = QHBoxLayout()
        self.build = QPushButton("Build Table from Hit")
        self.build.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.build)
        layout.addLayout(bottom)
        self.bind_run(self.run, self.stop, self.status, "Searching")
        self.run.clicked.connect(self._search)
        self.query.returnPressed.connect(self._search)
        self.results.itemSelectionChanged.connect(self._on_select)
        self.results.itemDoubleClicked.connect(lambda item: self._jump())
        self.build.clicked.connect(self._build)
        self.resize(640, 400)

    def set_data(self, data: bytes) -> None:
        self._data = data

    def _search(self) -> None:
        query = self.query.text()
        if not query or not self._data:
            self.status.setText("Open a ROM and type a query.")
            return
        with self.running():
            try:
                found = relative_search(
                    self._data,
                    query,
                    widths=self.width.currentData(),
                    case_gap=self.case_gap.isChecked(),
                    limit=self.limit.value(),
                    progress=self.progress,
                )
            except ValueError as exc:
                self._hits = []
                self.status.setText(str(exc))
            else:
                self._hits = found.hits
                note = ""
                if found.truncated:
                    note = " — the limit stopped the search; raise Limit for more"
                elif self.cancelled:
                    note = " (stopped)"
                self.status.setText(f"{len(self._hits)} hit(s){note}")
        self._fill()

    def _fill(self) -> None:
        self.results.fill(
            [
                f"{hit.offset:X}",
                f"{hit.width * 8}-bit {hit.endian}" if hit.width > 1 else "8-bit",
                " ".join(
                    f"{b:02X}" for b in self._data[hit.offset : hit.offset + hit.length]
                ),
                ", ".join(f"{k}={v:X}" for k, v in hit.bases.items()),
            ]
            for hit in self._hits
        )

    def _current_hit(self) -> Hit | None:
        return self.results.pick(self._hits)

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
