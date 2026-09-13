"""The Scan window: text-likeness regions under the start table."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.table import TableSet
from mapchar.engines.scan import Region, scan
from mapchar.ui.widgets import CancellableRun, ResultsTable
from mapchar.ui.window_layout import remember_layout


class ScanWindow(CancellableRun, QWidget):
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
        self.window_size = QSpinBox()
        self.window_size.setRange(8, 4096)
        self.window_size.setValue(64)
        self.step = QSpinBox()
        self.step.setRange(1, 4096)
        self.step.setValue(32)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.1, 1.0)
        self.threshold.setSingleStep(0.05)
        self.threshold.setValue(0.6)
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
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.results = ResultsTable(["Start", "End", "Score", "Terminator", "Initial"])
        layout.addWidget(self.results, 1)
        bottom = QHBoxLayout()
        self.block = QPushButton("New block from region")
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
                f"{r.terminator:02X}" if r.terminator is not None else "",
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
