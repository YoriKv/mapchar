"""The Scan window: text-likeness regions under the start table."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.table import TableSet
from mapchar.engines.scan import Region, scan
from mapchar.ui.number_fields import decimal_spin, number_spin
from mapchar.ui.widgets import CancellableRun, ElidedLabel, EscapeCloses, ResultsTable
from mapchar.ui.window_layout import remember_layout


def _strings_of(region: Region) -> str:
    """How the region cuts its strings, in the Block dialog's words: the end
    token it guessed, or the length prefix and the header in front of it."""
    if region.records is not None:
        header = region.records.header
        return "Length prefix" + (f", header {header}" if header else "")
    if region.terminator is not None:
        return f"End token {region.terminator:02X}"
    return ""


class ScanWindow(EscapeCloses, CancellableRun, QWidget):
    go_to = Signal(int, int)
    new_block = Signal(object)
    """A Region to make a block from."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Scan")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "scan_window")
        self._data: bytes = b""
        self._tables: TableSet | None = None
        self._regions: list[Region] = []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
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
        self.run = QPushButton("Scan")
        self.stop = QPushButton("Stop")
        row.addWidget(QLabel("Window"))
        row.addWidget(self.window_size)
        row.addWidget(QLabel("Step"))
        row.addWidget(self.step)
        row.addWidget(QLabel("Threshold"))
        row.addWidget(self.threshold)
        row.addStretch(1)
        row.addWidget(self.run)
        row.addWidget(self.stop)
        layout.addLayout(row)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)
        self.results = ResultsTable(["Start", "End", "Score", "Strings", "Initial"])
        layout.addWidget(self.results, 1)
        bottom = QHBoxLayout()
        self.block = QPushButton("New Block from Region")
        self.block.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.block)
        layout.addLayout(bottom)
        self.bind_run(self.run, self.stop, self.status, "Scanning")
        self.run.clicked.connect(self._scan)
        self.results.itemSelectionChanged.connect(self._on_select)
        self.block.clicked.connect(self._make_block)
        self.resize(560, 400)

    def set_source(self, data: bytes, tables: TableSet | None) -> None:
        self._data = data
        self._tables = tables

    def _scan(self) -> None:
        if self._tables is None or not self._data:
            self.status.setText("Open a ROM and pick a start table.")
            return
        with self.running():
            self._regions = scan(
                self._data,
                self._tables,
                window=self.window_size.value(),
                step=self.step.value(),
                threshold=self.threshold.value(),
                progress=self.progress,
            )
        self.status.setText(
            f"{len(self._regions)} region(s)" + (" (stopped)" if self.cancelled else "")
        )
        self.results.fill(
            [
                f"{r.start:X}",
                f"{r.end:X}",
                f"{r.score:.2f}",
                _strings_of(r),
                f"{r.initial:02X}" if r.initial is not None else "",
            ]
            for r in self._regions
        )

    def _current(self) -> Region | None:
        return self.results.pick(self._regions)

    def _on_select(self) -> None:
        region = self._current()
        self.block.setEnabled(region is not None)
        if region is not None:
            self.go_to.emit(region.start, region.length)

    def _make_block(self) -> None:
        region = self._current()
        if region is not None:
            self.new_block.emit(region)
