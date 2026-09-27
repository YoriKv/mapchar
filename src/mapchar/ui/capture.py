"""Capture's windows: the setup window before playing, the capture window,
the Captures dock with its Review tab, and the before-and-after of Confirm in
game.

Presentation only: the main window hands them the session's captures and the
proposals, and takes back what the user asks for — the setup to play with, the
text of a capture, a proposal accepted, edited or rejected, a glyph's label, a
change to see in the game.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mapchar.capture.consoles import Console
from mapchar.capture.occurrence import Finding
from mapchar.capture.proposals import BLOCK, CONFLICT, ENTRIES, GLYPH, Proposal
from mapchar.capture.session import BUSY, DONE, FAILED, Capture
from mapchar.capture.setup import ROM, Setup, ram_font, rom_font
from mapchar.ui.number_fields import AddressEdit, AddressSpelling
from mapchar.ui.widgets import ElidedLabel, ModeToggle

SETUP_WIDTH = 380
"""The setup window's width, which its note wraps to."""

THUMB = 96
"""The width of a capture's thumbnail in the dock."""

STATE_WORDS = {
    "waiting": "Waiting",
    "replaying": "Replaying",
    "finding": "Finding the text",
    "tracing": "Tracing",
    "done": "Done",
    "failed": "Failed",
}


def _pixmap(path: str | None, width: int) -> QPixmap:
    pix = QPixmap(path) if path else QPixmap()
    if pix.isNull():
        return pix
    return pix.scaledToWidth(width, Qt.TransformationMode.FastTransformation)


def finding_html(text: str, finding: Finding | None) -> str:
    """What a capture found wrong, with the typed words that did not fit
    underlined."""
    if finding is None:
        return ""
    message = html.escape(finding.message)
    if finding.kind != "no-match" or not finding.matched:
        return message
    out = html.escape(text)
    for word in finding.words:
        if word not in finding.matched:
            out = out.replace(
                html.escape(word), f"<u style='color:#d33'>{html.escape(word)}</u>"
            )
    return f"{message}<br>{out}"


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
        pix = QPixmap(capture.screenshot or "")
        if not pix.isNull():
            shot.setPixmap(
                pix.scaled(
                    pix.width() * 2,
                    pix.height() * 2,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
            )
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


class CapturesPanel(QWidget):
    """The Captures dock: every capture with its screenshot, typed text and
    state, and the Review tab of what they propose together."""

    text_requested = Signal(str)
    """Open the capture window for this capture."""
    retry_requested = Signal(str)
    stop_requested = Signal()
    remove_requested = Signal(str)
    review_requested = Signal()
    """Work the proposals out again."""
    accept_requested = Signal(str)
    edit_requested = Signal(str)
    reject_requested = Signal(str)
    label_requested = Signal(str, str)
    """A glyph proposal's id and the text typed for it."""
    confirm_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.captures: list[Capture] = []
        self.proposals: list[Proposal] = []
        self.states: dict[str, str] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        page = QWidget()
        col = QVBoxLayout(page)
        self.list = QListWidget()
        self.list.setObjectName("captures_list")
        self.list.setIconSize(self.list.iconSize().expandedTo(self._thumb_size()))
        self.list.itemDoubleClicked.connect(
            lambda item: self.text_requested.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        self.list.currentItemChanged.connect(self._sync_buttons)
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
        self.review = QListWidget()
        self.review.setObjectName("review_list")
        self.review.currentItemChanged.connect(self._show_proposal)
        split.addWidget(self.review)
        self.detail = QTextBrowser()
        split.addWidget(self.detail)
        col.addWidget(split)
        label_row = QHBoxLayout()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Text this glyph stands for")
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
        self.confirm_button = QPushButton("Confirm in Game")
        self.refresh_button = QPushButton("Refresh")
        for b in (
            self.accept_button,
            self.edit_button,
            self.reject_button,
            self.confirm_button,
            self.refresh_button,
        ):
            row.addWidget(b)
        self.accept_button.clicked.connect(
            lambda: self._emit_proposal(self.accept_requested)
        )
        self.edit_button.clicked.connect(
            lambda: self._emit_proposal(self.edit_requested)
        )
        self.reject_button.clicked.connect(
            lambda: self._emit_proposal(self.reject_requested)
        )
        self.confirm_button.clicked.connect(
            lambda: self._emit_proposal(self.confirm_requested)
        )
        self.refresh_button.clicked.connect(self.review_requested.emit)
        col.addLayout(row)
        self.tabs.addTab(review, "Review")
        self.tabs.currentChanged.connect(
            lambda i: self.review_requested.emit() if i == 1 else None
        )
        self._sync_buttons()
        self._show_proposal()

    @staticmethod
    def _thumb_size():
        from PySide6.QtCore import QSize

        return QSize(THUMB, THUMB * 7 // 8)

    # -- captures

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _emit_selected(self, signal) -> None:
        cid = self._selected()
        if cid:
            signal.emit(cid)

    def show_captures(self, captures: list[Capture], messages: list[str] = ()) -> None:
        current = self._selected()
        self.captures = list(captures)
        self.list.blockSignals(True)
        self.list.clear()
        for cap in self.captures:
            text = cap.text or "(type the text)"
            state = STATE_WORDS.get(cap.state, cap.state)
            if cap.state in BUSY and cap.status:
                state += f": {cap.status}"
            elif cap.state == FAILED and cap.reason:
                state += f": {cap.reason}"
            elif cap.state == DONE and cap.result is not None:
                r = cap.result
                state += f": {len(r.sources)} of {r.outputs} traced"
            item = QListWidgetItem(f"{text}\n{state}")
            item.setData(Qt.ItemDataRole.UserRole, cap.id)
            pix = _pixmap(cap.screenshot, THUMB)
            if not pix.isNull():
                item.setIcon(pix)
            self.list.addItem(item)
            if cap.id == current:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self._show_work()
        self.status.setText("<br>".join(html.escape(m) for m in messages))
        self._sync_buttons()

    def _show_work(self) -> None:
        """The capture being worked on, and how far through its step: a bar
        that fills while the step is counted, and moves while it is not."""
        cap = next((c for c in self.captures if c.state in BUSY), None)
        self.work.setVisible(cap is not None)
        self.progress.setVisible(cap is not None)
        if cap is None:
            return
        state = STATE_WORDS.get(cap.state, cap.state)
        if cap.status:
            state += f": {cap.status}"
        self.work.setText(f"“{cap.text}” — {state}")
        done, total = cap.progress or (0, 0)
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _sync_buttons(self, *_):
        cid = self._selected()
        cap = next((c for c in self.captures if c.id == cid), None)
        self.text_button.setEnabled(cap is not None)
        self.retry_button.setEnabled(
            cap is not None and cap.state == FAILED and bool(cap.text)
        )
        self.stop_button.setEnabled(any(c.state in BUSY for c in self.captures))
        self.remove_button.setEnabled(cap is not None)

    # -- review

    def show_proposals(self, proposals: list[Proposal], states: dict[str, str]) -> None:
        current = self.review.currentItem()
        keep = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.proposals = list(proposals)
        self.states = dict(states)
        self.review.blockSignals(True)
        self.review.clear()
        for p in self.proposals:
            mark = {"accepted": "✓ ", "rejected": "✗ "}.get(
                self.states.get(p.id, ""), ""
            )
            item = QListWidgetItem(mark + p.title)
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            if p.unconfirmed:
                item.setToolTip("Unconfirmed: " + "; ".join(p.unconfirmed))
            self.review.addItem(item)
            if p.id == keep:
                self.review.setCurrentItem(item)
        self.review.blockSignals(False)
        self._show_proposal()

    def _proposal(self) -> Proposal | None:
        item = self.review.currentItem()
        if item is None:
            return None
        pid = item.data(Qt.ItemDataRole.UserRole)
        return next((p for p in self.proposals if p.id == pid), None)

    def _emit_proposal(self, signal) -> None:
        p = self._proposal()
        if p is not None:
            signal.emit(p.id)

    def _show_proposal(self, *_):
        p = self._proposal()
        self.accept_button.setEnabled(
            p is not None and p.kind in (BLOCK, ENTRIES, CONFLICT)
        )
        self.edit_button.setEnabled(p is not None and p.kind in (BLOCK, ENTRIES))
        self.reject_button.setEnabled(p is not None)
        self.confirm_button.setEnabled(
            p is not None and p.kind in (BLOCK, GLYPH, ENTRIES)
        )
        glyph = p is not None and p.kind == GLYPH
        self.label_edit.setEnabled(glyph)
        self.label_button.setEnabled(glyph)
        if glyph and p.meaning is not None:
            self.label_edit.setText(p.meaning.text)
        if p is None:
            self.detail.setHtml(
                "<p>Finished captures propose blocks, table entries and glyphs "
                "to label here.</p>"
            )
            return
        parts = [f"<b>{html.escape(p.title)}</b>"]
        if p.detail:
            parts.append(html.escape(p.detail))
        if p.kind == ENTRIES:
            lines = [f"{e.bits}: {e.kind.value} {e.text}" for e in p.entries[:200]]
            parts.append("<pre>" + html.escape("\n".join(lines)) + "</pre>")
        if p.unconfirmed:
            parts.append(
                "<b>Unconfirmed</b><ul>"
                + "".join(f"<li>{html.escape(u)}</li>" for u in p.unconfirmed)
                + "</ul>"
            )
        parts.append("Captures: " + html.escape(", ".join(p.captures)))
        self.detail.setHtml("<br>".join(parts))

    def _label(self) -> None:
        p = self._proposal()
        if p is not None and p.kind == GLYPH and self.label_edit.text():
            self.label_requested.emit(p.id, self.label_edit.text())
