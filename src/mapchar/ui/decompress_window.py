"""The Decompressed view: what a scheme yields from the current offset."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from mapchar.ui.raw_widget import RawWidget, RowModel


class DecompressWindow(QWidget):
    jump_next = Signal()
    scan_next = Signal()
    to_block = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setWindowTitle("Decompressed view")
        layout = QVBoxLayout(self)
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.raw = RawWidget()
        layout.addWidget(self.raw, 1)
        row = QHBoxLayout()
        self.next = QPushButton("Jump to Next")
        self.scan = QPushButton("Scan")
        self.block = QPushButton("To Block")
        row.addWidget(self.next)
        row.addWidget(self.scan)
        row.addStretch(1)
        row.addWidget(self.block)
        layout.addLayout(row)
        self.next.clicked.connect(self.jump_next)
        self.scan.clicked.connect(self.scan_next)
        self.block.clicked.connect(self.to_block)
        self.resize(760, 360)

    def show_result(self, model: RowModel | None, status: str, complete: bool) -> None:
        self.raw.set_model(model)
        self.status.setText(status)
        self.block.setEnabled(model is not None and complete)
        self.next.setEnabled(model is not None and complete)
