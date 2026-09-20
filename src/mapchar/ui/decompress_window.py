"""The Decompressed view: what a scheme yields from the current offset."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.tokens import Token
from mapchar.pipeline.scan import FoundStructure
from mapchar.pipeline.text_view import text_model
from mapchar.ui import setting_int, settings
from mapchar.ui.progress import CancellableRun
from mapchar.ui.raw_cells import RowModel
from mapchar.ui.raw_widget import RawWidget
from mapchar.ui.text_widget import TextWidget
from mapchar.ui.tool_window import ToolWindow
from mapchar.ui.widgets import ElidedLabel, ResultsTable

TAB_KEY = "decompress_window/tab"
"""Which reading the window was left on, remembered per machine beside its
geometry: the payload is looked at the same way from one structure to the next."""


class DecompressWindow(ToolWindow, CancellableRun):
    """The floating view of what the picked scheme yields at the current offset.

    Three readings of the same slice, as tabs: the bytes, the text they decode
    to through the reading's table — the Hex and Text tabs the main window has
    for a file, fed from one decode — and the list of every structure **Find
    All** found in the file.

    Scan walks forward over the whole file one offset at a time and Find All
    over the whole of it once, which is long enough to need a way out:
    Run/Stop and the progress line are
    :class:`~mapchar.ui.progress.CancellableRun`'s, as in the Search and Scan
    windows, and :meth:`set_scanning` disables everything else, so nothing can be
    asked of a window whose offset is about to move.

    The window is opened either way round: by hand from the menu
    (:meth:`keep_open`), or by a scheme arming itself where the view landed
    (:meth:`show_armed`). Only the second kind hides itself again
    (:meth:`hide_if_armed`); the first stays, and says in both readings that
    nothing decodes here (:meth:`show_nothing`), since a window that vanished
    would take Find All with it.
    """

    jump_next = Signal()
    scan_next = Signal()
    to_block = Signal()
    find_all = Signal()
    go_to = Signal(int)
    """A structure the list names: show the file there."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            "Decompressed View",
            "decompress_window",
            (760, 360),
            parent,
            Qt.WindowType.Tool,
        )
        self._structures: list[FoundStructure] = []
        self._tokens: list[Token] = []
        self._window = b""
        """The bytes the tabs show, kept so a switch of what the text shows
        re-renders them without another decode."""
        layout = QVBoxLayout(self)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.raw = RawWidget()
        self.text = TextWidget()
        hex_pane, self.hex_note = self._reading_tab(self.raw)
        text_pane, self.text_note = self._reading_tab(self.text)
        self._readings = (
            (hex_pane, self.raw, self.hex_note),
            (text_pane, self.text, self.text_note),
        )
        """Each reading as its tab, the view in it, and the note shown in the
        view's place while nothing decodes."""
        self.tabs.addTab(hex_pane, "Hex")
        self.tabs.addTab(text_pane, "Text")
        self.tabs.addTab(self._structures_tab(), "Structures")
        layout.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        self.next = QPushButton("Jump to Next")
        self.next.setToolTip("Move to the byte after the structure shown")
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
        self.find_button.clicked.connect(self.find_all)
        self.results.itemSelectionChanged.connect(self._on_structure)
        # The same tokens read to another text; nothing is decoded again.
        self.text.shown_changed.connect(self._show_text)
        self.text.fit_changed.connect(self._show_text)
        self.bind_run(self.scan, self.stop, self.status, "Scanning")
        self._scanning = False
        self._armed_open = False
        """Whether the window is on screen only because a scheme armed itself
        where the view is; one opened by hand stays open."""
        self.tabs.setCurrentIndex(setting_int(TAB_KEY, 0, low=0))
        self.tabs.currentChanged.connect(
            lambda index: settings().setValue(TAB_KEY, index)
        )

    def _reading_tab(self, view: QWidget) -> tuple[QStackedWidget, QLabel]:
        """One reading of the payload, or a line in its place when there is none.

        The note takes the whole tab rather than sitting above the view: an
        empty dump beside "nothing decodes here" reads as a payload of zero
        bytes, which is a different thing. Its size is ignored so a message of
        any length leaves the window as small as its controls need.
        """
        pane = QStackedWidget()
        note = QLabel()
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        pane.addWidget(view)
        pane.addWidget(note)
        return pane, note

    def _structures_tab(self) -> QWidget:
        """The Structures tab: Find All over its list of what it found."""
        pane = QWidget()
        box = QVBoxLayout(pane)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.find_button = QPushButton("Find All")
        self.find_button.setToolTip("Look through the whole file for structures")
        self.found = ElidedLabel("")
        row.addWidget(self.find_button)
        row.addWidget(self.found, 1)
        box.addLayout(row)
        self.results = ResultsTable(["Offset", "Packed", "Size", "Text"])
        box.addWidget(self.results, 1)
        return pane

    # -- opening and closing ----------------------------------------------------

    def keep_open(self) -> None:
        """Opened by hand: from here it stays open, whatever decodes."""
        self._armed_open = False

    def show_armed(self) -> None:
        """Show the window because a scheme armed itself where the view is.

        A window already on screen is left as it is: what opened it is what
        decides whether it closes itself again.
        """
        if not self.isVisible():
            self._armed_open = True
            self.show()

    def hide_if_armed(self) -> None:
        """Take back a window that opened itself, now that nothing decodes.

        Not one the user opened, not one a walk is running in, and not one
        holding a list of structures: the list is the whole file's and outlives
        the offset the view happens to sit on.
        """
        if self._armed_open and not self._scanning and not self._structures:
            self.hide()

    def set_scanning(self, active: bool) -> None:
        """Freeze everything a running scan does not drive, and thaw it.

        Stop is :meth:`~mapchar.ui.progress.CancellableRun.running`'s to swap
        with whichever button started the walk; every other control that starts
        one or reads the position it is about to move is switched off here. The
        structure buttons come back under :meth:`show_result`, which the refresh
        after the scan calls, so nothing here re-arms a button the new position
        does not justify.
        """
        self._scanning = active
        self.next.setEnabled(False)
        self.block.setEnabled(False)
        self.scan.setEnabled(not active)
        self.find_button.setEnabled(not active)
        self.tabs.setEnabled(not active)

    def show_result(
        self,
        model: RowModel | None,
        tokens: list[Token],
        status: str,
        complete: bool,
    ) -> None:
        """Show one decode: its bytes, the text they read as, and how it went.

        ``tokens`` are the window's own, in step with ``model``'s bytes — one
        decode of the payload, rendered twice, as the main window's Hex and
        Text tabs are.
        """
        self.raw.set_model(model)
        # Another payload, so a Shift+click has nothing left to reach from.
        self.raw.clear_anchor()
        self._tokens = tokens
        self._window = model.data if model is not None else b""
        self._show_text()
        self.status.setText(status)
        self._show_notes(False)
        # A scan's own progress refreshes run through here; while one is running
        # the only live control is Stop.
        live = model is not None and complete and not self._scanning
        self.block.setEnabled(live)
        self.next.setEnabled(live)

    def show_nothing(self, message: str) -> None:
        """Nothing decodes where the view is: say so in place of both readings.

        What a window the user opened shows for as long as it takes to get
        somewhere a scheme reads, so the two tabs answer the question the empty
        window otherwise leaves open. The structure list is untouched: it is the
        file's, not this offset's.
        """
        # An empty decode, which is what clears the two views and the buttons
        # over them, and then the message in the views' place.
        self.show_result(None, [], message, False)
        for _pane, _view, note in self._readings:
            note.setText(message)
        self._show_notes(True)

    def _show_notes(self, showing: bool) -> None:
        """Put the message in front of the Hex and Text readings, or the
        readings back in front of it."""
        for pane, view, note in self._readings:
            pane.setCurrentWidget(note if showing else view)

    def _show_text(self) -> None:
        """Render the kept tokens as text, as the box now shows them."""
        if not self._window:
            self.text.set_model(None)
            return
        length = len(self._window)
        self.text.set_model(text_model(self._tokens, 0, length, self.text.shown()))
        self.text.set_position(0, (0, length))

    # -- the structures ---------------------------------------------------------

    def set_structures(self, structures: list[FoundStructure], note: str) -> None:
        """Show what Find All found: one row per structure, ``note`` above them."""
        self._structures = list(structures)
        self.found.setText(note)
        self.results.fill(
            [
                f"{s.offset:X}",
                f"{s.consumed:,}",
                f"{s.size:,}",
                f"{s.score:.2f}",
            ]
            for s in self._structures
        )

    def clear_structures(self) -> None:
        """Drop the list: it is one file's, and another file is on screen."""
        self.set_structures([], "")

    def _on_structure(self) -> None:
        found = self.results.pick(self._structures)
        if found is not None:
            self.go_to.emit(found.offset)
