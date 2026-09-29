"""Capture's windows: the setup window before playing, the capture window,
the Captures dock with its Review tab, the before-and-after of Confirm in
game, and a table's strings shown in the game.

Presentation only: the main window hands them the session's captures and the
proposals, and takes back what the user asks for — the setup to play with, the
text of a capture, a proposal accepted, edited, rejected or reset, a glyph's
label, a change to see in the game.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.capture.consoles import Console
from mapchar.capture.occurrence import Finding
from mapchar.capture.proposals import BLOCK, CONFLICT, ENTRIES, GLYPH, Proposal
from mapchar.capture.session import BUSY, DONE, FAILED, Capture
from mapchar.capture.setup import ROM, Setup, ram_font, rom_font
from mapchar.core.block import PointerTableSource
from mapchar.ui.number_fields import AddressEdit, AddressSpelling
from mapchar.ui.widgets import ElidedLabel, ModeToggle, show_elided_tooltips

SETUP_WIDTH = 380
"""The setup window's width, which its note wraps to."""

THUMB = 96
"""The width of a capture's thumbnail in the dock."""

SHOT_WIDTH = 256
"""The width a string's screenshot is shown at."""

SHOT_COLUMNS = 3

STATE_WORDS = {
    "waiting": "Waiting",
    "replaying": "Replaying",
    "finding": "Finding the text",
    "tracing": "Tracing",
    "done": "Done",
    "failed": "Failed",
}

RECORDER_WORDS = {
    "connected": "Recorder connected: pause the game to capture the text on screen.",
    "waiting": "Waiting for the recorder in the emulator to connect…",
    "closed": "The emulator closed.",
}

NO_SESSION = (
    "Capture ▸ Captures opens the captures of the ROM on screen; Capture ▸ Play "
    "in Emulator… plays it."
)
NO_CAPTURES = (
    "No captures yet: Capture ▸ Play in Emulator… runs the game, and pausing "
    "it captures the text on screen."
)

ACCEPTED = "accepted"
REJECTED = "rejected"

_LETTER = r"[^\W\d_]"


def _pixmap(path: str | None, width: int | None = None, scale: int = 1) -> QPixmap:
    """A screenshot, scaled to ``width`` or by ``scale``; null when there is
    none."""
    pix = QPixmap(path) if path else QPixmap()
    if pix.isNull():
        return pix
    fast = Qt.TransformationMode.FastTransformation
    if width is not None:
        return pix.scaledToWidth(width, fast)
    if scale != 1:
        return pix.scaled(
            pix.width() * scale,
            pix.height() * scale,
            Qt.AspectRatioMode.KeepAspectRatio,
            fast,
        )
    return pix


def finding_html(text: str, finding: Finding | None) -> str:
    """What a capture found wrong, with the typed words that did not fit
    underlined — whole words only, found in one pass over the typed text."""
    if finding is None:
        return ""
    message = html.escape(finding.message)
    if finding.kind != "no-match" or not finding.matched:
        return message
    bad = sorted(
        {w for w in finding.words if w and w not in finding.matched},
        key=len,
        reverse=True,
    )
    if not bad:
        return f"{message}<br>{html.escape(text)}"
    pattern = re.compile(
        rf"(?<!{_LETTER})(?:{'|'.join(map(re.escape, bad))})(?!{_LETTER})"
    )
    out, at = [], 0
    for m in pattern.finditer(text):
        out.append(html.escape(text[at : m.start()]))
        out.append(f"<u style='color:#d33'>{html.escape(m.group())}</u>")
        at = m.end()
    out.append(html.escape(text[at:]))
    return f"{message}<br>{''.join(out)}"


def _keys(view: QWidget, keys, slot) -> None:
    """Keys that act on ``view``'s current row while it has the focus."""
    for key in keys:
        sc = QShortcut(QKeySequence(key), view)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(slot)


class CaptureSetupWindow(QDialog):
    """What the user already knows about the game, given before playing:
    where its font is, in the ROM or in RAM. Everything is optional; Play
    starts the emulator with it, and :attr:`setup` holds it."""

    def __init__(
        self,
        setup: Setup,
        console: Console,
        rom_size: int,
        shift: int,
        spelling: AddressSpelling | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setup = setup
        self.console, self.rom_size, self.shift = console, rom_size, shift
        self.setWindowTitle("Capture Setup")
        self.setMinimumWidth(SETUP_WIDTH)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Console", QLabel(console.name))
        layout.addLayout(form)

        self.font_box = QGroupBox("Font")
        self.font_box.setCheckable(True)
        self.font_box.setToolTip(
            "Where the game's font is: the first read of it after a pause in "
            "its drawing marks the start of a text"
        )
        box = QFormLayout(self.font_box)
        self.memory = ModeToggle((("ROM", ROM), ("RAM", "ram")))
        self.memory.button(ROM).setToolTip("Offsets as mapchar shows them")
        self.memory.button("ram").setToolTip(
            "Addresses as the console's bus has them, as the game's code reads them"
        )
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.memory)
        mode_row.addStretch(1)
        box.addRow("In", mode_row)
        # A page of fields for each memory, one over the other, so switching
        # moves nothing: ROM offsets spelled as the window spells addresses,
        # RAM addresses always flat.
        self.pages = QStackedWidget()
        self.rom_start, self.rom_end = AddressEdit(spelling), AddressEdit(spelling)
        flat = AddressSpelling(self)
        self.ram_start, self.ram_end = AddressEdit(flat), AddressEdit(flat)
        for start, end in (
            (self.rom_start, self.rom_end),
            (self.ram_start, self.ram_end),
        ):
            page = QWidget()
            rows = QFormLayout(page)
            rows.setContentsMargins(0, 0, 0, 0)
            rows.addRow("Start", start)
            rows.addRow("End", end)
            self.pages.addWidget(page)
        box.addRow(self.pages)
        note = QLabel(
            "Its first read after a pause in drawing marks where a text starts, "
            "so the text can be captured long after it appears. A font in video "
            "memory is never read this way."
        )
        note.setWordWrap(True)
        box.addRow(note)
        self.memory.chosen.connect(self._show_memory)
        layout.addWidget(self.font_box)

        self.message = ElidedLabel()
        layout.addWidget(self.message)
        buttons = QDialogButtonBox()
        self.play_button = buttons.addButton(
            "Play", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.play_button.clicked.connect(self._play)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load(setup)

    def _load(self, setup: Setup) -> None:
        f = setup.font
        self.font_box.setChecked(f is not None)
        if f is None:
            self._show_memory(ROM)
            return
        if f.memory == ROM:
            self.rom_start.set_value(f.start + self.shift)
            self.rom_end.set_value(f.end + self.shift)
            self._show_memory(ROM)
        else:
            bus = f.start if f.bus is None else f.bus
            self.ram_start.set_value(bus)
            self.ram_end.set_value(bus + f.end - f.start)
            self._show_memory("ram")

    def _show_memory(self, memory: str) -> None:
        self.memory.set_value(memory)
        self.pages.setCurrentIndex(0 if memory == ROM else 1)

    def read(self) -> Setup:
        """The setup as the window has it; ``ValueError`` says what does not
        read."""
        if not self.font_box.isChecked():
            return Setup()
        rom = self.memory.value() == ROM
        start, end = (
            (self.rom_start, self.rom_end) if rom else (self.ram_start, self.ram_end)
        )
        a, b = start.value(), end.value()
        if a is None or b is None:
            raise ValueError("Give the font's start and end.")
        if rom:
            return Setup(rom_font(a - self.shift, b - self.shift, self.rom_size))
        return Setup(ram_font(self.console, a, b))

    def _play(self) -> None:
        try:
            self.setup = self.read()
        except ValueError as e:
            self.message.setText(str(e))
            return
        self.accept()


class CaptureWindow(QDialog):
    """The screenshot and a text box: type what the game shows, all of it or
    part, then Capture or Skip. Not modal — play may go on."""

    captured = Signal(str, str)
    """The capture's id and the text typed."""
    skipped = Signal(str)

    def __init__(self, capture: Capture, parent: QWidget | None = None):
        super().__init__(parent)
        self.capture = capture
        self.setWindowTitle("Capture")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        shot = QLabel()
        shot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = _pixmap(capture.screenshot, scale=2)
        if not pix.isNull():
            shot.setPixmap(pix)
        else:
            shot.setText("(no screenshot)")
        layout.addWidget(shot)
        layout.addWidget(QLabel("Type the text the game shows — all of it, or part:"))
        self.text = QLineEdit(capture.text)
        self.text.setObjectName("capture_text")
        layout.addWidget(self.text)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.message)
        if capture.finding is not None:
            self.show_finding(capture.finding)
        buttons = QDialogButtonBox()
        self.capture_button = buttons.addButton(
            "Capture", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.skip_button = buttons.addButton(
            "Skip", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        later = buttons.addButton("Later", QDialogButtonBox.ButtonRole.RejectRole)
        # Enter in the text box captures, once: no button also takes it as
        # the dialog's default.
        for b in (self.capture_button, self.skip_button, later):
            b.setAutoDefault(False)
            b.setDefault(False)
        self.capture_button.clicked.connect(self._capture)
        self.skip_button.clicked.connect(self._skip)
        later.clicked.connect(self.close)
        layout.addWidget(buttons)
        self.text.returnPressed.connect(self._capture)

    def show_finding(self, finding: Finding) -> None:
        self.message.setText(finding_html(self.text.text(), finding))

    def _capture(self) -> None:
        if self.text.text().strip():
            self.captured.emit(self.capture.id, self.text.text().strip())

    def _skip(self) -> None:
        self.skipped.emit(self.capture.id)
        self.close()


class ConfirmDialog(QDialog):
    """A proposal shown in the game: the frame before the change and after."""

    def __init__(
        self, title: str, before: str, after: str, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        for label, path in (("Before", before), ("After", after)):
            col = QVBoxLayout()
            col.addWidget(QLabel(label))
            img = QLabel()
            pix = _pixmap(path, 512)
            img.setPixmap(pix) if not pix.isNull() else img.setText("(none)")
            col.addWidget(img)
            row.addLayout(col)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class StringShotsDialog(QDialog):
    """A pointer table's strings in the game: a screenshot each, as they come,
    with how far the run is, Stop, and Show More past the strings one run
    shows."""

    stop_requested = Signal()
    more_requested = Signal(int)
    """Show the strings from this index on."""

    def __init__(self, title: str, total: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(SHOT_WIDTH * SHOT_COLUMNS + 80, 640)
        self.total = total
        self.running = False
        self.first = self.next = 0
        self.cells: dict[int, QLabel] = {}
        layout = QVBoxLayout(self)
        note = QLabel(
            "Each string is shown through the captured text's pointer, at the "
            "frame the captured text had settled. A string that waits for a "
            "button, or takes longer to draw, is shown as it stands then; one "
            "the text box cannot draw may show nothing, or draw wrong."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        host = QWidget()
        self.grid = QGridLayout(host)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = ElidedLabel()
        layout.addWidget(self.status)
        buttons = QDialogButtonBox()
        self.stop_button = buttons.addButton(
            "Stop", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.more_button = buttons.addButton(
            "Show More", QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.stop_button.clicked.connect(self._stop)
        self.more_button.clicked.connect(lambda: self.more_requested.emit(self.next))
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.more_button.setEnabled(False)

    def start(self, first: int, end: int) -> None:
        self.running, self.first, self.next = True, first, first
        self.progress.setRange(0, max(1, end - first))
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText(
            f"Running strings {first + 1}–{end} of {self.total} in the game…"
        )
        self.stop_button.setEnabled(True)
        self.more_button.setEnabled(False)

    def add_shot(self, shot: tuple[int, str]) -> None:
        index, path = shot
        cell = QWidget()
        col = QVBoxLayout(cell)
        img = QLabel()
        pix = _pixmap(path, SHOT_WIDTH)
        img.setPixmap(pix) if not pix.isNull() else img.setText("(no screenshot)")
        col.addWidget(img)
        col.addWidget(QLabel(f"String {index + 1}"))
        n = len(self.cells)
        self.grid.addWidget(cell, n // SHOT_COLUMNS, n % SHOT_COLUMNS)
        self.cells[index] = img
        self.next = index + 1
        self.progress.setValue(self.next - self.first)

    def _ended(self, text: str) -> None:
        self.running = False
        self.progress.hide()
        self.status.setText(text)
        self.stop_button.setEnabled(False)
        self.more_button.setEnabled(self.next < self.total)

    def finish(self, end: int) -> None:
        self.next = end
        self._ended(f"Strings 1–{end} of {self.total} shown.")

    def stopped(self) -> None:
        self._ended(f"Stopped after string {self.next} of {self.total}.")

    def fail(self, message: str) -> None:
        self._ended(f"Stopped: {message}")

    def _stop(self) -> None:
        if self.running:
            self.stop_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.running:
            self.stop_requested.emit()
        super().closeEvent(event)


class CapturesPanel(QWidget):
    """The Captures dock: every capture with its screenshot, typed text and
    state, the recorder's connection, and the Review tab of what they propose
    together."""

    text_requested = Signal(str)
    """Open the capture window for this capture."""
    retry_requested = Signal(str)
    stop_requested = Signal()
    remove_requested = Signal(str)
    stop_playing_requested = Signal()
    review_requested = Signal()
    """Work the proposals out again."""
    accept_requested = Signal(str)
    accept_all_requested = Signal()
    edit_requested = Signal(str)
    reject_requested = Signal(str)
    reset_requested = Signal(str)
    label_requested = Signal(str, str)
    """A glyph proposal's id and the text typed for it."""
    confirm_requested = Signal(str)
    sweep_requested = Signal(str)
    """Show the strings of a block's pointer table in the game."""
    goto_requested = Signal(str)

    def __init__(
        self, spelling: AddressSpelling | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.spelling = spelling
        self.captures: list[Capture] = []
        self.proposals: list[Proposal] = []
        self.states: dict[str, str] = {}
        self.target = ""
        self.new_target = False
        self.side = False
        """A run in the game (Confirm, a table's strings) is under way."""
        self._ids: list[str] = []
        self._thumbs: dict[str, QIcon | None] = {}
        self._seen: dict[str, str] = {}
        self._glyph_group: QTreeWidgetItem | None = None
        self._review_ids: list[str] = []
        self._review_rows: dict[str, QTreeWidgetItem] = {}
        self._label_pid: str | None = None
        """The proposal the label field was last filled for."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        page = QWidget()
        col = QVBoxLayout(page)
        self.hint = QLabel(NO_SESSION)
        self.hint.setObjectName("captures_hint")
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setObjectName("captures_list")
        self.list.setIconSize(self.list.iconSize().expandedTo(self._thumb_size()))
        self.list.itemDoubleClicked.connect(
            lambda item: self.text_requested.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        self.list.currentItemChanged.connect(self._sync_buttons)
        show_elided_tooltips(self.list)
        _keys(
            self.list,
            (Qt.Key.Key_Return, Qt.Key.Key_Enter),
            lambda: self._emit_selected(self.text_requested),
        )
        _keys(
            self.list,
            (Qt.Key.Key_Delete,),
            lambda: self._emit_selected(self.remove_requested),
        )
        col.addWidget(self.list)
        self.work = QLabel()
        self.work.setObjectName("capture_work")
        self.work.setWordWrap(True)
        col.addWidget(self.work)
        self.progress = QProgressBar()
        self.progress.setObjectName("capture_progress")
        col.addWidget(self.progress)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        col.addWidget(self.status)
        rec = QHBoxLayout()
        self.recorder = ElidedLabel()
        self.recorder.setObjectName("capture_recorder")
        self.stop_playing_button = QPushButton("Stop Playing")
        self.stop_playing_button.clicked.connect(self.stop_playing_requested.emit)
        rec.addWidget(self.recorder, 1)
        rec.addWidget(self.stop_playing_button)
        col.addLayout(rec)
        row = QHBoxLayout()
        self.text_button = QPushButton("Type Text…")
        self.retry_button = QPushButton("Retry")
        self.stop_button = QPushButton("Stop")
        self.remove_button = QPushButton("Remove")
        for b in (
            self.text_button,
            self.retry_button,
            self.stop_button,
            self.remove_button,
        ):
            row.addWidget(b)
        self.text_button.clicked.connect(
            lambda: self._emit_selected(self.text_requested)
        )
        self.retry_button.clicked.connect(
            lambda: self._emit_selected(self.retry_requested)
        )
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.remove_button.clicked.connect(
            lambda: self._emit_selected(self.remove_requested)
        )
        col.addLayout(row)
        self.tabs.addTab(page, "Captures")

        review = QWidget()
        col = QVBoxLayout(review)
        split = QSplitter(Qt.Orientation.Vertical)
        self.review = QTreeWidget()
        self.review.setObjectName("review_list")
        self.review.setHeaderHidden(True)
        self.review.currentItemChanged.connect(self._show_proposal)
        show_elided_tooltips(self.review)
        _keys(
            self.review,
            (Qt.Key.Key_Return, Qt.Key.Key_Enter),
            self._enter_proposal,
        )
        _keys(self.review, (Qt.Key.Key_Delete,), self._delete_proposal)
        split.addWidget(self.review)
        self.detail = QTextBrowser()
        split.addWidget(self.detail)
        col.addWidget(split)
        side = QHBoxLayout()
        self.side_line = ElidedLabel()
        self.side_line.setObjectName("capture_side")
        self.side_stop_button = QPushButton("Stop")
        self.side_stop_button.clicked.connect(self.stop_requested.emit)
        side.addWidget(self.side_line, 1)
        side.addWidget(self.side_stop_button)
        col.addLayout(side)
        label_row = QHBoxLayout()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Text this glyph stands for")
        self.label_edit.setToolTip("Text this glyph stands for")
        self.label_button = QPushButton("Label")
        self.label_button.clicked.connect(self._label)
        self.label_edit.returnPressed.connect(self._label)
        label_row.addWidget(self.label_edit)
        label_row.addWidget(self.label_button)
        col.addLayout(label_row)
        row = QHBoxLayout()
        self.accept_button = QPushButton("Accept")
        self.edit_button = QPushButton("Edit…")
        self.reject_button = QPushButton("Reject")
        self.reset_button = QPushButton("Reset")
        self.goto_button = QPushButton("Go To")
        for b in (
            self.accept_button,
            self.edit_button,
            self.reject_button,
            self.reset_button,
            self.goto_button,
        ):
            row.addWidget(b)
        col.addLayout(row)
        row = QHBoxLayout()
        self.confirm_button = QPushButton("Confirm in Game")
        self.sweep_button = QPushButton("Show Its Strings in Game")
        self.accept_all_button = QPushButton("Accept All Unreviewed")
        self.refresh_button = QPushButton("Refresh")
        for b in (
            self.confirm_button,
            self.sweep_button,
            self.accept_all_button,
            self.refresh_button,
        ):
            row.addWidget(b)
        col.addLayout(row)
        for button, signal in (
            (self.accept_button, self.accept_requested),
            (self.edit_button, self.edit_requested),
            (self.reject_button, self.reject_requested),
            (self.reset_button, self.reset_requested),
            (self.goto_button, self.goto_requested),
            (self.confirm_button, self.confirm_requested),
            (self.sweep_button, self.sweep_requested),
        ):
            button.clicked.connect(lambda _=False, s=signal: self._emit_proposal(s))
        self.accept_all_button.clicked.connect(self.accept_all_requested.emit)
        self.refresh_button.clicked.connect(self.review_requested.emit)
        self.tabs.addTab(review, "Review")
        self.tabs.currentChanged.connect(
            lambda i: self.review_requested.emit() if i == 1 else None
        )
        self.show_no_session()
        self.set_side(None)

    @staticmethod
    def _thumb_size() -> QSize:
        return QSize(THUMB, THUMB * 7 // 8)

    # -- captures

    def show_no_session(self) -> None:
        """No captures are open: an empty list, and how to open some."""
        self.show_captures([], [])
        self.show_recorder("")
        self.show_proposals([], {})
        self.hint.setText(NO_SESSION)

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _emit_selected(self, signal) -> None:
        cid = self._selected()
        if cid:
            signal.emit(cid)

    def _thumb(self, cap: Capture) -> QIcon | None:
        if cap.id not in self._thumbs:
            pix = _pixmap(cap.screenshot, THUMB)
            self._thumbs[cap.id] = None if pix.isNull() else QIcon(pix)
        return self._thumbs[cap.id]

    def show_captures(
        self, captures: list[Capture], messages: list[str] | None = None
    ) -> None:
        """Show the captures: rows are added and removed only as captures
        come and go, and each row's words are brought up to date in place. A
        capture that has just failed is selected, its reason in its tooltip."""
        self.captures = list(captures)
        ids = [c.id for c in self.captures]
        if ids != self._ids:
            current = self._selected()
            self.list.blockSignals(True)
            self.list.clear()
            for cap in self.captures:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, cap.id)
                self.list.addItem(item)
                if cap.id == current:
                    self.list.setCurrentItem(item)
            self.list.blockSignals(False)
            self._ids = ids
            for gone in set(self._thumbs) - set(ids):
                del self._thumbs[gone]
        failed = None
        for row, cap in enumerate(self.captures):
            item = self.list.item(row)
            text, tip = self._words(cap)
            if item.text() != text:
                item.setText(text)
            if item.toolTip() != tip:
                item.setToolTip(tip)
            if item.icon().isNull():
                icon = self._thumb(cap)
                if icon is not None:
                    item.setIcon(icon)
            was = self._seen.get(cap.id)
            if was is not None and was != FAILED and cap.state == FAILED:
                failed = item
            self._seen[cap.id] = cap.state
        if failed is not None:
            self.list.setCurrentItem(failed)
            self.list.scrollToItem(failed)
            QApplication.alert(self.window())
        self.hint.setText(NO_CAPTURES)
        self.hint.setVisible(not self.captures)
        self.show_work()
        if messages is not None:
            self.status.setText("<br>".join(html.escape(m) for m in messages))
        self._sync_buttons()

    @staticmethod
    def _words(cap: Capture) -> tuple[str, str]:
        """A capture's row: its text and state, and its tooltip."""
        text = cap.text or "(type the text)"
        state = STATE_WORDS.get(cap.state, cap.state)
        if cap.state in BUSY and cap.status:
            state += f": {cap.status}"
        elif cap.state == FAILED and cap.reason:
            state += f": {cap.reason}"
        elif cap.state == DONE and cap.result is not None:
            r = cap.result
            state += f": {len(r.sources)} of {r.outputs} traced"
        tip = ""
        if cap.state == FAILED:
            tip = finding_html(cap.text, cap.finding) or html.escape(cap.reason)
        return f"{text}\n{state}", tip

    def show_work(self) -> None:
        """The capture being worked on, and how far through its step: a bar
        that fills while the step is counted, and moves while it is not."""
        cap = next((c for c in self.captures if c.state in BUSY), None)
        self.work.setVisible(cap is not None)
        self.progress.setVisible(cap is not None)
        if cap is not None:
            state = STATE_WORDS.get(cap.state, cap.state)
            if cap.status:
                state += f": {cap.status}"
            self.work.setText(f"“{cap.text}” — {state}")
            done, total = cap.progress or (0, 0)
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.stop_button.setEnabled(cap is not None or self.side)

    def show_recorder(self, state: str, handed_over: bool = False) -> None:
        """The recorder's connection: ``connected``, ``waiting``, ``closed``,
        or nothing when the game has not been played."""
        words = RECORDER_WORDS.get(state, "")
        if state == "connected" and handed_over:
            words = "Recorder connected, in a Mesen window that was already open."
        self.recorder.setText(words)
        self.recorder.setVisible(bool(words))
        self.stop_playing_button.setVisible(bool(words))
        self.stop_playing_button.setEnabled(state in ("connected", "waiting"))

    def _sync_buttons(self, *_):
        cid = self._selected()
        cap = next((c for c in self.captures if c.id == cid), None)
        self.text_button.setEnabled(cap is not None)
        self.retry_button.setEnabled(
            cap is not None and cap.state == FAILED and bool(cap.text)
        )
        self.stop_button.setEnabled(
            self.side or any(c.state in BUSY for c in self.captures)
        )
        self.remove_button.setEnabled(cap is not None)

    # -- review

    def show_proposals(
        self,
        proposals: list[Proposal],
        states: dict[str, str],
        target: str = "",
        new_target: bool = False,
    ) -> None:
        """The proposals, glyphs to label gathered under one row; ``states``
        says which are accepted or rejected, and ``target`` names the table
        accepted entries go to (``new_target``: one made for them). The same
        proposals again only bring their rows up to date, the selection and a
        label being typed left as they are."""
        self.proposals = list(proposals)
        self.states = dict(states)
        self.target, self.new_target = target, new_target
        ids = [p.id for p in self.proposals]
        if ids != self._review_ids:
            self._rebuild_review()
            self._review_ids = ids
        self._update_review()
        self._show_proposal()

    def _rebuild_review(self) -> None:
        keep = self._current_pid()
        was_open = self._glyph_group is not None and self._glyph_group.isExpanded()
        self.review.blockSignals(True)
        self.review.clear()
        self._glyph_group = None
        self._review_rows = {}
        glyphs = []
        for p in self.proposals:
            item = QTreeWidgetItem([p.title])
            item.setData(0, Qt.ItemDataRole.UserRole, p.id)
            self._review_rows[p.id] = item
            if p.kind == GLYPH:
                glyphs.append(item)
            else:
                self.review.addTopLevelItem(item)
        if glyphs:
            self._glyph_group = QTreeWidgetItem([""])
            self.review.addTopLevelItem(self._glyph_group)
            self._glyph_group.addChildren(glyphs)
            self._glyph_group.setExpanded(was_open)
        if keep in self._review_rows:
            self.review.setCurrentItem(self._review_rows[keep])
        self.review.blockSignals(False)

    def _update_review(self) -> None:
        """Each row's mark and tooltip, in place."""
        for p in self.proposals:
            item = self._review_rows[p.id]
            text = self._mark(p) + p.title
            if item.text(0) != text:
                item.setText(0, text)
            tip = "Unconfirmed: " + "; ".join(p.unconfirmed) if p.unconfirmed else ""
            if item.toolTip(0) != tip:
                item.setToolTip(0, tip)
        if self._glyph_group is not None:
            glyphs = [p for p in self.proposals if p.kind == GLYPH]
            open_ = sum(1 for p in glyphs if not self.states.get(p.id))
            self._glyph_group.setText(
                0, f"Glyphs to label: {open_} of {len(glyphs)} open"
            )

    def _mark(self, p: Proposal) -> str:
        return {ACCEPTED: "✓ ", REJECTED: "✗ "}.get(self.states.get(p.id, ""), "")

    def review_items(self) -> list[QTreeWidgetItem]:
        """Every proposal's row, in order, the glyphs' inside their group."""
        out = []
        for i in range(self.review.topLevelItemCount()):
            top = self.review.topLevelItem(i)
            if top.data(0, Qt.ItemDataRole.UserRole):
                out.append(top)
            out += [top.child(k) for k in range(top.childCount())]
        return out

    def select_proposal(self, pid: str) -> None:
        for item in self.review_items():
            if item.data(0, Qt.ItemDataRole.UserRole) == pid:
                self.review.setCurrentItem(item)
                return

    def _current_pid(self) -> str | None:
        item = self.review.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _proposal(self) -> Proposal | None:
        pid = self._current_pid()
        return next((p for p in self.proposals if p.id == pid), None)

    def _emit_proposal(self, signal) -> None:
        p = self._proposal()
        if p is not None:
            signal.emit(p.id)

    def set_side(self, text: str | None) -> None:
        """A run in the game under way, and what it is; None when none is."""
        self.side = text is not None
        self.side_line.setText(text or "")
        self.side_line.setVisible(text is not None)
        self.side_stop_button.setVisible(text is not None)
        self._show_proposal()
        self._sync_buttons()

    @staticmethod
    def has_table(p: Proposal | None) -> bool:
        return (
            p is not None
            and p.kind == BLOCK
            and p.config is not None
            and isinstance(p.config.source, PointerTableSource)
        )

    def _show_proposal(self, *_):
        p = self._proposal()
        state = self.states.get(p.id, "") if p is not None else ""
        accepted = state == ACCEPTED
        self.accept_button.setEnabled(
            p is not None and p.kind in (BLOCK, ENTRIES, CONFLICT) and not accepted
        )
        self.edit_button.setEnabled(
            p is not None and p.kind in (BLOCK, ENTRIES) and not accepted
        )
        self.reject_button.setEnabled(p is not None and not state)
        self.reset_button.setEnabled(state == REJECTED)
        self.goto_button.setEnabled(p is not None and p.kind == BLOCK)
        self.confirm_button.setEnabled(
            p is not None and p.kind in (BLOCK, GLYPH) and not self.side
        )
        self.sweep_button.setEnabled(self.has_table(p) and not self.side)
        self.accept_all_button.setEnabled(
            any(
                q.kind in (BLOCK, ENTRIES) and not self.states.get(q.id)
                for q in self.proposals
            )
        )
        glyph = p is not None and p.kind == GLYPH
        self.label_edit.setEnabled(glyph and not accepted)
        self.label_button.setEnabled(glyph and not accepted)
        pid = p.id if p is not None else None
        if pid != self._label_pid:  # a label being typed stays while it is on
            self._label_pid = pid
            self.label_edit.setText(p.meaning.text if glyph and p.meaning else "")
        if p is None:
            self.detail.setHtml(
                "<p>Finished captures propose blocks, table entries and glyphs "
                "to label here.</p>"
            )
            return
        parts = [f"<b>{html.escape(p.title)}</b>"]
        if accepted:
            parts.append(
                "<b>Accepted</b>: the project has it. Undo, or remove it, to "
                "accept it again."
            )
        elif state == REJECTED:
            parts.append("<b>Rejected</b>: Reset puts it back for review.")
        if p.detail:
            parts.append(html.escape(p.detail))
        if p.kind == BLOCK:
            where = (
                self.spelling.format(p.offset)
                if self.spelling is not None
                else f"${p.offset:X}"
            )
            parts.append(f"Strings from {html.escape(where)}")
        if p.kind in (BLOCK, ENTRIES, GLYPH, CONFLICT) and self.target:
            new = " (a new table)" if self.new_target else ""
            parts.append(f"Table: @{html.escape(self.target)}{new}")
        if p.kind == ENTRIES:
            lines = [f"{e.bits}: {e.kind.value} {e.text}" for e in p.entries[:200]]
            parts.append("<pre>" + html.escape("\n".join(lines)) + "</pre>")
        notes = p.unconfirmed
        if notes:
            parts.append(
                "<b>Unconfirmed</b><ul>"
                + "".join(f"<li>{html.escape(str(u))}</li>" for u in notes)
                + "</ul>"
            )
        parts.append("Captures: " + html.escape(", ".join(p.captures)))
        self.detail.setHtml("<br>".join(parts))

    def _enter_proposal(self) -> None:
        item = self.review.currentItem()
        if item is not None and item.childCount():
            item.setExpanded(not item.isExpanded())
            return
        p = self._proposal()
        if p is None:
            return
        if p.kind == GLYPH:
            if self.label_edit.isEnabled():
                self.label_edit.setFocus()
        elif self.accept_button.isEnabled():
            self.accept_requested.emit(p.id)

    def _delete_proposal(self) -> None:
        p = self._proposal()
        if p is not None and self.reject_button.isEnabled():
            self.reject_requested.emit(p.id)

    def _label(self) -> None:
        p = self._proposal()
        if p is not None and p.kind == GLYPH and self.label_edit.text():
            self.label_requested.emit(p.id, self.label_edit.text())
