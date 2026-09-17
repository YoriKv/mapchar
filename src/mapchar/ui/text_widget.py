"""The text mode of the central view: decoded text as an ordinary text box.

Presentation only, on the pattern of celPix's text window: it holds a body
and a map from characters to bytes, never decodes anything itself, and
reports selections as byte ranges — and a click that selects nothing as none —
so the aligned view and the status bar can follow. It shows text and never
takes an edit; text is edited in the Strings view.

The box holds a window sized to itself, like the raw view's rows: it says how
much of a body is in view (:meth:`TextWidget.fitted_chars`), and whoever feeds
it cuts the window to that. So it never scrolls on its own — the wheel, the keys
and the scrollbar beside it move the view instead, by lines to where a line of
it starts (:meth:`TextWidget.line_starts`) — and the box's own scrollbars are
fixed, since one that came and went with the content would change the room, and
with it the window, and with it the content. The bar beside the box is the
file's — or the stretch of it the view is confined to — as the Hex tab's is:
its handle is the window, its arrows step a line, its trough a page, and a drag
goes to the byte under the handle.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)

from mapchar.pipeline.text_view import Shown, TextModel
from mapchar.ui.widgets import apply_wrap, mono_font, setting_toggle, wheel_steps

WORD_WRAP_KEY = "view/text_word_wrap"
SHOW_CODES_KEY = "view/text_show_codes"
SHOW_UNKNOWN_KEY = "view/text_show_unknown"


class TextWidget(QWidget):
    selection_changed = Signal(int, int)
    """Absolute byte range selected, end exclusive; (-1, -1) for none."""
    fit_changed = Signal()
    """The room for text changed — the box was resized, its font changed or
    Wrap was switched — so a different window fits it now."""
    shown_changed = Signal()
    """Show codes or Show unknown was switched: the same bytes read to a
    different text now."""
    scroll_requested = Signal(int)
    """The wheel turned over the box, or the scrollbar's arrow was clicked:
    move the view by this many lines, down for positive."""
    page_requested = Signal(int)
    """The scrollbar's trough was clicked: move the view a page, down for
    positive."""
    offset_requested = Signal(int)
    """The scrollbar was dragged: start the view at this byte."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._model: TextModel | None = None
        self._syncing = False
        self._wheel_rest = 0
        """What a wheel turned short of a notch, kept for the next turn."""
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFont(mono_font())
        self.edit.setUndoRedoEnabled(False)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit.installEventFilter(self)
        self.edit.viewport().installEventFilter(self)
        self.bar = QScrollBar(Qt.Orientation.Vertical)
        self.bar.setSingleStep(1)
        self.bar.actionTriggered.connect(self._on_bar_action)
        self.bar.valueChanged.connect(self._on_bar_value)
        self._placing = False
        self.wrap = setting_toggle(
            "Wrap",
            "Wrap long lines to the window's width",
            WORD_WRAP_KEY,
            self._apply_wrap,
        )
        self.show_codes = setting_toggle(
            "Show codes",
            "Show codes like [end] or [color 3] in the text; "
            "hidden, their line breaks stay",
            SHOW_CODES_KEY,
            self._on_shown,
        )
        self.show_unknown = setting_toggle(
            "Show unknown",
            "Show bytes no table entry matches, as [$XX]",
            SHOW_UNKNOWN_KEY,
            self._on_shown,
        )
        self.note = QLabel("")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.note, 1)
        bar.addWidget(self.show_codes)
        bar.addWidget(self.show_unknown)
        bar.addWidget(self.wrap)
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.edit, 1)
        box.addWidget(self.bar)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(box, 1)
        layout.addLayout(bar)
        self._apply_wrap(self.wrap.isChecked())
        self.edit.selectionChanged.connect(self._on_selection)
        self.edit.cursorPositionChanged.connect(self._on_selection)
        self._reported: tuple[int, int] | None = None

    def _on_shown(self, _on: bool) -> None:
        self.shown_changed.emit()

    def shown(self) -> Shown:
        """What of the decode the box shows, as its checkboxes say."""
        return Shown(self.show_codes.isChecked(), self.show_unknown.isChecked())

    def _apply_wrap(self, on: bool) -> None:
        apply_wrap(self.edit, on)
        self.fit_changed.emit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        kind = event.type()
        if watched is self.edit.viewport():
            if kind == QEvent.Type.Resize:
                self.fit_changed.emit()
            elif kind == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                zooming = event.modifiers() & Qt.KeyboardModifier.ControlModifier
                if delta and not zooming:
                    steps, self._wheel_rest = wheel_steps(self._wheel_rest, delta)
                    if steps:
                        self.scroll_requested.emit(-steps * 3)
                    return True
            elif kind == QEvent.Type.MouseButtonRelease:
                # A click where the cursor already was moves nothing, so no
                # signal says it selected nothing.
                self._on_selection()
        elif watched is self.edit and kind == QEvent.Type.FontChange:
            self.fit_changed.emit()
        return super().eventFilter(watched, event)

    # --- the scrollbar ------------------------------------------------------

    def set_position(self, offset: int, bounds: tuple[int, int]) -> None:
        """Place the scrollbar: the window starts at ``offset`` of the bytes
        ``bounds`` — the whole file, or the stretch the view is confined to.
        Mid-drag only the handle's place is kept, since a handle that changed
        its length under the mouse would jump."""
        bar = self.bar
        start, end = bounds
        self._placing = True
        try:
            if not bar.isSliderDown():
                shown = max(1, self.shown_bytes())
                bar.setRange(start, max(start, end - shown, offset))
                bar.setPageStep(shown)
            bar.setValue(offset)
        finally:
            self._placing = False

    def _on_bar_action(self, action: int) -> None:
        """The arrows and the trough step by lines and pages, which only the
        text knows, not by bytes: the handle stays until the view has moved."""
        slider = QScrollBar.SliderAction
        steps = {
            slider.SliderSingleStepSub.value: (self.scroll_requested, -1),
            slider.SliderSingleStepAdd.value: (self.scroll_requested, 1),
            slider.SliderPageStepSub.value: (self.page_requested, -1),
            slider.SliderPageStepAdd.value: (self.page_requested, 1),
        }
        if action in steps:
            self.bar.setSliderPosition(self.bar.value())
            signal, direction = steps[action]
            signal.emit(direction)

    def _on_bar_value(self, value: int) -> None:
        if not self._placing:
            self.offset_requested.emit(value)

    # --- the room for text ------------------------------------------------

    def lines_in_view(self) -> int:
        """How many lines of the face the box has room for, at least one."""
        margin = self.edit.document().documentMargin()
        room = self.edit.viewport().height() - 2 * margin
        return max(1, int(room // self.edit.fontMetrics().lineSpacing()))

    def room(self) -> int:
        """How many characters the box has room for: its lines by its columns.

        A first guess at the bytes that fill it, for whoever decodes them.
        """
        margin = self.edit.document().documentMargin()
        width = self.edit.viewport().width() - 2 * margin
        columns = max(1, int(width // self.edit.fontMetrics().horizontalAdvance("0")))
        return self.lines_in_view() * columns

    def fitted_chars(self) -> int:
        """How many characters of the body are in view, from the top.

        Whole lines only: a line whose bottom is under the box's edge is not in
        view. With Wrap off, a line wider than the box is in view as far as its
        right edge — for the last line that is where the window ends, and a
        line above it is kept whole, since the lines under it are in view and
        the window cannot skip its tail; the sideways scroll reaches it.
        """
        model, edit = self._model, self.edit
        if model is None:
            return 0
        viewport = edit.viewport()
        margin = edit.document().documentMargin()
        height, width = viewport.height() - margin, viewport.width()
        fitted = 0
        block = edit.firstVisibleBlock()
        shift = edit.contentOffset()
        while block.isValid():
            top = edit.blockBoundingGeometry(block).translated(shift).topLeft()
            if top.y() >= height:
                break
            layout = block.layout()
            cut = False
            for i in range(layout.lineCount()):
                line = layout.lineAt(i)
                rect = line.rect().translated(top)
                if rect.bottom() > height:
                    return fitted
                end = block.position() + line.textStart() + line.textLength()
                # The line's own rect is clipped to the box; its natural
                # width is what an unwrapped line really spans.
                cut = rect.left() + line.naturalTextWidth() > width
                if cut:
                    edge = QPoint(width - 1, int(rect.center().y()))
                    end = min(end, edit.cursorForPosition(edge).position())
                fitted = end
            if not cut:
                # The block's own line break is in view with its last line.
                fitted = min(block.position() + block.length(), len(model.body))
            block = block.next()
        return fitted

    def room_below(self) -> bool:
        """Whether the box has room for another line under the body's last.

        What tells a body that overflows the box from one that only ran out:
        unwrapped, a last line wider than the box is cut at its edge either
        way, and only a body with room under it may go on to more lines.
        """
        edit = self.edit
        block = edit.document().lastBlock()
        edit.document().documentLayout().blockBoundingRect(block)
        bottom = edit.blockBoundingGeometry(block).translated(edit.contentOffset())
        margin = edit.document().documentMargin()
        spacing = edit.fontMetrics().lineSpacing()
        return bottom.bottom() + spacing <= edit.viewport().height() - margin

    def line_starts(self) -> list[int]:
        """Where each line of the body begins, as laid out in the box: a wrapped
        line as much as one a line break starts. Every line, not only those in
        view."""
        document = self.edit.document()
        layout = document.documentLayout()
        starts: list[int] = []
        block = document.firstBlock()
        while block.isValid():
            # Asking for a block's rect lays it out; the box lays out lazily.
            layout.blockBoundingRect(block)
            lines = block.layout()
            if lines.lineCount() == 0:
                starts.append(block.position())
            for i in range(lines.lineCount()):
                starts.append(block.position() + lines.lineAt(i).textStart())
            block = block.next()
        return starts

    def shown_bytes(self) -> int:
        """How many bytes the text in view covers; none without a model."""
        return self._model.length if self._model is not None else 0

    def set_model(self, model: TextModel | None) -> None:
        self._model = model
        self._syncing = True
        try:
            if model is None:
                self.edit.setPlainText("")
                self.note.setText("")
            elif model.body != self.edit.toPlainText():
                self.edit.setPlainText(model.body)
            if model is not None:
                self.note.setText(
                    f"{model.offset:X}–{model.offset + model.length - 1:X}, "
                    f"{len(model.body):,} characters"
                )
        finally:
            self._syncing = False

    # --- selection sync ---------------------------------------------------

    def byte_at_char(self, pos: int) -> int | None:
        """The byte the token at character ``pos`` starts at: the first token
        there, so one that shows nothing — a table switch — is not stepped
        over. Past the body, the byte after it."""
        model = self._model
        if model is None or not model.spans:
            return None
        starts, ends, byte_starts, _ = model.columns()
        i = bisect_left(starts, pos)
        if i < len(starts) and starts[i] == pos:
            return byte_starts[i]
        if i > 0 and pos < ends[i - 1]:
            return byte_starts[i - 1]
        if pos >= model.spans[-1][1]:
            return model.spans[-1][3]
        return None

    def _on_selection(self) -> None:
        if self._syncing or self._model is None:
            return
        cursor = self.edit.textCursor()
        a, b = sorted((cursor.anchor(), cursor.position()))
        if a == b:
            if self._reported is not None:
                self._reported = None
                self.selection_changed.emit(-1, -1)
            return
        start = self.byte_at_char(a)
        end = None
        starts, ends, _, byte_ends = self._model.columns()
        # The first token ending at or after b is the one that holds it.
        i = bisect_left(ends, b)
        if i < len(ends) and starts[i] < b:
            end = byte_ends[i]
        if start is None or end is None or end <= start:
            return
        if self._reported == (start, end):
            return
        self._reported = (start, end)
        self.selection_changed.emit(start, end)

    def select_bytes(self, start: int, end: int) -> None:
        """Highlight the characters whose bytes fall in ``[start, end)``; an
        empty range highlights none."""
        model = self._model
        if model is None:
            return
        starts, ends, byte_starts, byte_ends = model.columns()
        # The tokens whose bytes touch the range: the first ending past its
        # start to the last starting before its end.
        first, last = bisect_right(byte_ends, start), bisect_left(byte_starts, end)
        self._syncing = True
        try:
            cursor = self.edit.textCursor()
            if first >= last:
                cursor.clearSelection()
            else:
                cursor.setPosition(starts[first])
                cursor.setPosition(ends[last - 1], QTextCursor.MoveMode.KeepAnchor)
            self.edit.setTextCursor(cursor)
            self._reported = (start, end) if end > start else None
        finally:
            self._syncing = False
