"""The Decompressed view: what a scheme yields from the current offset."""

from __future__ import annotations

from collections.abc import Callable

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

from mapchar.core.address import format_hex
from mapchar.core.tokens import Token
from mapchar.pipeline.structures import FoundStructure
from mapchar.pipeline.text_view import text_model
from mapchar.ui import DUMP_WINDOW_BYTES, setting_int, settings
from mapchar.ui.number_fields import AddressSpelling
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
    for a file — and the list of every structure **Find All** found in the
    file. The Hex tab counts from the payload's first byte, since the payload
    is nowhere in the file, and scrolls over all of it, a window at a time, each
    read where it starts; the Text tab holds the first window's text whole and
    scrolls it as a text box. The Structures tab's offsets are the file's, spelled
    as every other address the window shows.

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

    def __init__(
        self, spelling: AddressSpelling | None = None, parent: QWidget | None = None
    ):
        super().__init__(
            "Decompressed View",
            "decompress_window",
            (760, 360),
            parent,
            Qt.WindowType.Tool,
        )
        self._spelling = spelling
        self._structures: list[FoundStructure] = []
        self._note = ""
        self._payload = b""
        self._decode: Callable[[bytes, int], list[Token]] | None = None
        """How a window of the payload reads, given its bytes and the byte of
        the payload it starts at; the Hex tab asks it as it scrolls."""
        self._tokens: list[Token] = []
        self._window = b""
        """The bytes the Text tab shows, kept so a switch of what the text shows
        re-renders them without another decode."""
        layout = QVBoxLayout(self)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        self.raw = RawWidget()
        self.text = TextWidget()
        self.text.scroll_itself()
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
        self.next.setToolTip("Go to the byte after this structure")
        self.scan = QPushButton("Scan")
        self.scan.setToolTip("Scan forward for a structure that decompresses whole")
        self.stop = QPushButton("Stop")
        self.block = QPushButton("To Block…")
        self.block.setToolTip("Create a block over this structure")
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
        self.raw.offset_requested.connect(self._show_rows)
        if spelling is not None:
            spelling.changed.connect(lambda _old: self._fill_structures())
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
        self.find_button.setToolTip("Scan the whole file for structures")
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
        payload: bytes | None,
        decode: Callable[[bytes, int], list[Token]] | None,
        status: str,
        complete: bool,
    ) -> None:
        """Show one decode: its bytes, the text they read as, and how it went.

        ``decode`` reads a window of ``payload`` — its bytes, and the byte of
        the payload it starts at — as the main window's Hex tab reads one of the
        file. The first window is read once, for both tabs.
        """
        self._payload = payload or b""
        self._decode = decode
        self._window = self._payload[:DUMP_WINDOW_BYTES]
        self._tokens = decode(self._window, 0) if decode and self._window else []
        self.raw.set_model(
            RowModel(0, self._window, self._tokens, set(), len(self._payload))
            if payload is not None
            else None
        )
        # Another payload, so a Shift+click has nothing left to reach from.
        self.raw.clear_anchor()
        self._show_text()
        self.status.setText(status)
        self._show_notes(False)
        # A scan's own progress refreshes run through here; while one is running
        # the only live control is Stop.
        live = payload is not None and complete and not self._scanning
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
        self.show_result(None, None, message, False)
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

    def _show_rows(self, offset: int) -> None:
        """The Hex tab scrolled: hand it the payload's window from ``offset``,
        read where it starts."""
        if self._decode is None:
            return
        window = self._payload[offset : offset + DUMP_WINDOW_BYTES]
        tokens = self._decode(window, offset) if window else []
        self.raw.set_model(RowModel(offset, window, tokens, set(), len(self._payload)))

    # -- the structures ---------------------------------------------------------

    def set_structures(self, structures: list[FoundStructure], note: str) -> None:
        """Show what Find All found: one row per structure, ``note`` above them."""
        self._structures = list(structures)
        self._note = note
        self._fill_structures()

    def _fill_structures(self) -> None:
        """One row per structure, under the note: again when the address format
        changes, since the offsets are spelled in it."""
        spell = self._spelling.format if self._spelling is not None else format_hex
        self.found.setText(self._note)
        self.results.fill(
            [
                spell(s.offset),
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
