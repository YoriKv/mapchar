"""The Decompressed view: what a scheme yields from the current offset."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from mapchar.ui.raw_widget import RawWidget, RowModel
from mapchar.ui.widgets import CancellableRun, ElidedLabel, EscapeCloses
from mapchar.ui.window_layout import remember_layout


class DecompressWindow(EscapeCloses, CancellableRun, QWidget):
    """The floating view of what the picked scheme yields at the current offset.

    Scan walks forward over the whole file one offset at a time, which is long
    enough to need a way out: Run/Stop and the progress line are
    :class:`~mapchar.ui.widgets.CancellableRun`'s, as in the Search and Scan
    windows, and :meth:`set_scanning` disables everything else, so nothing can be
    asked of a window whose offset is about to move.
    """

    jump_next = Signal()
    scan_next = Signal()
    to_block = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setWindowTitle("Decompressed View")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "decompress_window")
        layout = QVBoxLayout(self)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)
        self.raw = RawWidget()
        layout.addWidget(self.raw, 1)
        row = QHBoxLayout()
        self.next = QPushButton("Jump to Next")
        self.next.setToolTip("Move the view to the next offset the scheme accepts")
        self.scan = QPushButton("Scan")
        self.scan.setToolTip("Walk forward until a structure decompresses whole")
        self.stop = QPushButton("Stop")
        self.block = QPushButton("To Block…")
        self.block.setToolTip("Make a block over the structure shown")
        row.addWidget(self.next)
        row.addWidget(self.scan)
        row.addWidget(self.stop)
        row.addStretch(1)
        row.addWidget(self.block)
        layout.addLayout(row)
        self.next.clicked.connect(self.jump_next)
        self.scan.clicked.connect(self.scan_next)
        self.block.clicked.connect(self.to_block)
        self.bind_run(self.scan, self.stop, self.status, "Scanning")
        self._scanning = False
        self.resize(760, 360)

    def set_scanning(self, active: bool) -> None:
        """Freeze everything a running scan does not drive, and thaw it.

        Scan and Stop are :meth:`~mapchar.ui.widgets.CancellableRun.running`'s
        to swap; what is left is the rest of the window. The structure buttons
        come back under :meth:`show_result`, which the refresh after the scan
        calls, so nothing here re-arms a button the new position does not
        justify.
        """
        self._scanning = active
        self.next.setEnabled(False)
        self.block.setEnabled(False)
        self.raw.setEnabled(not active)

    def show_result(self, model: RowModel | None, status: str, complete: bool) -> None:
        self.raw.set_model(model)
        self.status.setText(status)
        # A scan's own progress refreshes run through here; while one is running
        # the only live control is Stop.
        live = model is not None and complete and not self._scanning
        self.block.setEnabled(live)
        self.next.setEnabled(live)
