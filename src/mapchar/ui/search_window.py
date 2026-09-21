"""The Search window: find bytes or text, and relative search."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from mapchar.core.bits import format_hex_bytes
from mapchar.engines.relsearch import Hit, relative_search
from mapchar.ui.number_fields import number_spin
from mapchar.ui.tool_window import ResultsRunWindow
from mapchar.ui.widgets import fit_chars, hint_field


class SearchWindow(ResultsRunWindow):
    build_table = Signal(object)
    """A Hit to seed a table from."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            "Search",
            "search_window",
            (640, 400),
            run_label="Search",
            verb="Searching",
            headers=["Offset", "Width", "Bytes", "Bases"],
            action_label="Build Table from Hit",
            parent=parent,
        )
        self._data: bytes = b""
        self.query.returnPressed.connect(self._run)
        self.results.itemDoubleClicked.connect(lambda item: self._jump())

    def _parameters(self, row: QHBoxLayout) -> None:
        self.query = hint_field(
            QLineEdit(),
            "letters, digits, kana, ? wildcard",
            "Relative search: letters, digits or kana of a word the game shows; ? "
            "matches any character (Enter)",
        )
        fit_chars(self.query, 16)
        self.widths = QComboBox()
        self.widths.addItem("8-bit", (1,))
        self.widths.addItem("16-bit", (2,))
        self.widths.addItem("8 and 16-bit", (1, 2))
        self.widths.setToolTip("Bytes per character code")
        self.case_gap = QCheckBox("Case gap")
        self.case_gap.setChecked(True)
        self.case_gap.setToolTip("Allow any distance between upper and lower case")
        self.limit = number_spin(1, 1_000_000, 5, value=500)
        self.limit.setSingleStep(500)
        self.limit.setToolTip("Stop after this many hits")
        row.addWidget(self.query, 1)
        row.addWidget(self.widths)
        row.addWidget(self.case_gap)
        row.addWidget(QLabel("Limit"))
        row.addWidget(self.limit)

    def set_data(self, data: bytes) -> None:
        self._data = data

    def _run(self) -> None:
        query = self.query.text()
        if not query or not self._data:
            self.status.setText("Open a ROM and type a query.")
            return
        hits: list[Hit] = []
        with self.running():
            try:
                found = relative_search(
                    self._data,
                    query,
                    widths=self.widths.currentData(),
                    case_gap=self.case_gap.isChecked(),
                    limit=self.limit.value(),
                    progress=self.progress,
                )
            except ValueError as exc:
                self.status.setText(str(exc))
            else:
                hits = found.hits
                note = ""
                if found.truncated:
                    note = " — the limit stopped the search; raise Limit for more"
                elif self.cancelled:
                    note = " (stopped)"
                self.status.setText(f"{len(hits)} hit(s){note}")
        self.fill_results(hits)

    def _row(self, item: Hit) -> list[str]:
        return [
            f"{item.offset:X}",
            f"{item.width * 8}-bit {item.endian}" if item.width > 1 else "8-bit",
            format_hex_bytes(self._data[item.offset : item.offset + item.length]),
            ", ".join(f"{k}={v:X}" for k, v in item.bases.items()),
        ]

    def _span(self, item: Hit) -> tuple[int, int]:
        return item.offset, item.length

    def _act(self) -> None:
        hit = self.selected()
        if hit is not None:
            self.build_table.emit(hit)
