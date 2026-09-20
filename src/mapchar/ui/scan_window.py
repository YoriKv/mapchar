"""The Scan window: text-likeness regions under the start table."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from mapchar.core.table import TableSet
from mapchar.engines.scan import Region, scan
from mapchar.ui.number_fields import decimal_spin, number_spin
from mapchar.ui.tool_window import ResultsRunWindow


def _strings_of(region: Region) -> str:
    """How the region cuts its strings, in the Block dialog's words: the end
    token it guessed, or the length prefix and the header in front of it."""
    if region.records is not None:
        header = region.records.header
        return "Length prefix" + (f", header {header}" if header else "")
    if region.terminator is not None:
        return f"End token {region.terminator:02X}"
    return ""


class ScanWindow(ResultsRunWindow):
    new_block = Signal(object)
    """A Region to make a block from."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            "Scan",
            "scan_window",
            (560, 400),
            run_label="Scan",
            verb="Scanning",
            headers=["Start", "End", "Score", "Strings", "Initial"],
            action_label="New Block from Region",
            parent=parent,
        )
        self._data: bytes = b""
        self._tables: TableSet | None = None

    def _parameters(self, row: QHBoxLayout) -> None:
        self.window_size = number_spin(8, 4096, 4, value=64)
        self.window_size.setToolTip("Bytes scored at a time")
        self.step = number_spin(1, 4096, 4, value=32)
        self.step.setToolTip("Bytes the window moves between scores")
        self.threshold = decimal_spin(0.1, 1.0, 1, 2)
        self.threshold.setSingleStep(0.05)
        self.threshold.setValue(0.6)
        self.threshold.setToolTip(
            "Minimum text-likeness score, 0 to 1, for a window to count"
        )
        row.addWidget(QLabel("Window"))
        row.addWidget(self.window_size)
        row.addWidget(QLabel("Step"))
        row.addWidget(self.step)
        row.addWidget(QLabel("Threshold"))
        row.addWidget(self.threshold)
        row.addStretch(1)

    def set_source(self, data: bytes, tables: TableSet | None) -> None:
        self._data = data
        self._tables = tables

    def _run(self) -> None:
        if self._tables is None or not self._data:
            self.status.setText("Open a ROM and pick a start table.")
            return
        with self.running():
            regions = scan(
                self._data,
                self._tables,
                window=self.window_size.value(),
                step=self.step.value(),
                threshold=self.threshold.value(),
                progress=self.progress,
            )
        self.status.setText(
            f"{len(regions)} region(s)" + (" (stopped)" if self.cancelled else "")
        )
        self.fill_results(regions)

    def _row(self, item: Region) -> list[str]:
        return [
            f"{item.start:X}",
            f"{item.end:X}",
            f"{item.score:.2f}",
            _strings_of(item),
            f"{item.initial:02X}" if item.initial is not None else "",
        ]

    def _span(self, item: Region) -> tuple[int, int]:
        return item.start, item.length

    def _act(self) -> None:
        region = self.selected()
        if region is not None:
            self.new_block.emit(region)
