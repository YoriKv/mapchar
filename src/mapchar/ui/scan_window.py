"""The Scan window: text-likeness regions under the start table."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.table import TableSet
from mapchar.engines.scan import Region, scan


class ScanWindow(QWidget):
    go_to = Signal(int, int)
    new_block = Signal(object)
    """A Region to make a block from."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Scan")
        self._data: bytes = b""
        self._tables: TableSet | None = None
        self._regions: list[Region] = []
        self._cancel = False
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
        self.stop.setEnabled(False)
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
        self.results = QTableWidget(0, 5)
        self.results.setHorizontalHeaderLabels(
            ["Start", "End", "Score", "Terminator", "Initial"]
        )
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.results, 1)
        bottom = QHBoxLayout()
        self.block = QPushButton("New block from region")
        self.block.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.block)
        layout.addLayout(bottom)
        self.run.clicked.connect(self._scan)
        self.stop.clicked.connect(self._on_stop)
        self.results.itemSelectionChanged.connect(self._on_select)
        self.block.clicked.connect(self._make_block)
        self.resize(560, 400)

    def set_source(self, data: bytes, tables: TableSet | None) -> None:
        self._data = data
        self._tables = tables

    def _on_stop(self) -> None:
        self._cancel = True

    def _progress(self, done: int, total: int) -> bool:
        self.status.setText(f"Scanning… {done * 100 // max(total, 1)}%")
        QApplication.processEvents()
        return not self._cancel

    def _scan(self) -> None:
        if self._tables is None or not self._data:
            self.status.setText("Open a ROM and pick a start table.")
            return
        self._cancel = False
        self.run.setEnabled(False)
        self.stop.setEnabled(True)
        try:
            self._regions = scan(
                self._data,
                self._tables,
                window=self.window_size.value(),
                step=self.step.value(),
                threshold=self.threshold.value(),
                progress=self._progress,
            )
        finally:
            self.run.setEnabled(True)
            self.stop.setEnabled(False)
        self.status.setText(
            f"{len(self._regions)} region(s)" + (" (stopped)" if self._cancel else "")
        )
        self.results.setRowCount(len(self._regions))
        for row, r in enumerate(self._regions):
            cells = [
                f"{r.start:X}",
                f"{r.end:X}",
                f"{r.score:.2f}",
                f"{r.terminator:02X}" if r.terminator is not None else "",
                f"{r.initial:02X}" if r.initial is not None else "",
            ]
            for col, text in enumerate(cells):
                self.results.setItem(row, col, QTableWidgetItem(text))
        self.results.resizeColumnsToContents()

    def _current(self) -> Region | None:
        rows = self.results.selectionModel().selectedRows()
        return self._regions[rows[0].row()] if rows else None

    def _on_select(self) -> None:
        region = self._current()
        self.block.setEnabled(region is not None)
        if region is not None:
            self.go_to.emit(region.start, region.length)

    def _make_block(self) -> None:
        region = self._current()
        if region is not None:
            self.new_block.emit(region)
