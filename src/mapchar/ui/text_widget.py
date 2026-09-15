"""The text mode of the central view: decoded text as an ordinary text box.

Presentation only, on the pattern of celPix's text window: it holds a body
and a map from characters to bytes, never decodes anything itself, and
reports selections as byte ranges — and a click that selects nothing as none —
so the aligned view and the status bar can follow. Read-only until the Strings
view's editor exists.

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
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.table import TableSet, TokenKind
from mapchar.core.tokens import Token
from mapchar.engines.decode import RunResult
from mapchar.ui import settings
from mapchar.ui.widgets import mono_font

WORD_WRAP_KEY = "view/text_word_wrap"


@dataclass
class TextModel:
    body: str
    spans: list[tuple[int, int, int, int]]
    """``(char_start, char_end, byte_start, byte_end)`` per token, absolute bytes."""
    offset: int
    """The byte the text starts at."""
    length: int
    """How many bytes the text covers."""
    _columns: tuple[list[int], ...] | None = field(default=None, repr=False)

    def columns(self) -> tuple[list[int], list[int], list[int], list[int]]:
        """The spans by column — char starts, char ends, byte starts, byte
        ends — each in order, so a position is found by bisection rather than
        by walking every token of a window."""
        if self._columns is None:
            self._columns = tuple(
                list(column) for column in zip(*self.spans, strict=True)
            ) or ([], [], [], [])
        return self._columns

    def cut(self, kept: int) -> TextModel:
        """The first ``kept`` tokens, at least one, as a model of their own."""
        if kept >= len(self.spans):
            return self
        kept = max(kept, 1)
        last = self.spans[kept - 1]
        return TextModel(
            self.body[: last[1]], self.spans[:kept], self.offset, last[3] - self.offset
        )


def _byte_span(bit_start: int, bit_end: int) -> tuple[int, int]:
    """The absolute bytes a token over absolute bits touches, at least the one
    it starts in."""
    byte_start = bit_start // 8
    return byte_start, max(byte_start + 1, -(-bit_end // 8))


def text_model(tokens: list[Token], offset: int, length: int) -> TextModel:
    """Render tokens (bit positions relative to ``offset``) to a body and map."""
    parts: list[str] = []
    spans: list[tuple[int, int, int, int]] = []
    at = 0
    base = offset * 8
    for token in tokens:
        text = token.text()
        byte_start, byte_end = _byte_span(base + token.bit_start, base + token.bit_end)
        spans.append((at, at + len(text), byte_start, byte_end))
        parts.append(text)
        at += len(text)
    return TextModel("".join(parts), spans, offset, length)


class TextDecode:
    """The Text tab's decode, kept from one window to the next.

    A window that has moved a line down is nearly the window before it: the
    same tokens from a little further on, and a line's worth more at the end.
    So the tokens are kept, by their absolute bits, and a window is served from
    them wherever they reach; only what lies past them is decoded, from the
    last token boundary they can be trusted to. The text above a window, which
    a step up lays out to find the line to land on, is kept the same way: once
    decoded it joins the tokens in front, and a later step up over it decodes
    nothing.

    Decoding from a byte in the middle of the kept tokens gives the same tokens
    only where the decoder carries no state across a token: a set with no
    table switch, where every token boundary on a byte is a fresh start. With
    switches, the kept tokens serve a window from the byte they were decoded
    from and no other, as a fresh decode would.

    Tokens near a cut end are not to be trusted: a key cut short by the end of
    the data reads as unmatched bytes, and a code's operand as nothing. So a
    decode that goes further starts from the last token boundary at least
    ``margin`` bits — the longest key and operands of the set — before the cut.
    """

    def __init__(self, data: bytes, tables: TableSet, origin: int) -> None:
        self.data = data
        self.tables = tables
        self.origin = origin
        """The byte the first token starts at."""
        self.end = origin
        """The byte the decoded data ended at, exclusive."""
        self.starts: list[int] = []
        self.ends: list[int] = []
        """Each token's absolute bits."""
        self.byte_starts: list[int] = []
        self.byte_ends: list[int] = []
        """The absolute bytes each token touches, at least the one it starts in."""
        self.texts: list[str] = []
        self.chars: list[int] = [0]
        """The character each token's text starts at, and after the last the
        length of them all."""
        entries = [e for t in tables.tables.values() for e in t.entries.values()]
        self.resumable = all(e.kind is not TokenKind.SWITCH for e in entries)
        self.margin = max(
            (len(e.bits) + sum(o.bits for o in e.operands) for e in entries), default=8
        )

    def serves(self, data: bytes, tables: TableSet) -> bool:
        """Whether these tokens are of ``data`` read through ``tables``."""
        return (
            data is self.data
            and tables.start is self.tables.start
            and tables.tables == self.tables.tables
        )

    def _first(self, offset: int) -> int | None:
        """The index of the token a window from ``offset`` starts with, when
        one can: the token starting there, if decoding afresh from there would
        read the same."""
        if offset == self.origin:
            return 0
        if not self.resumable:
            return None
        i = bisect_left(self.starts, offset * 8)
        if i < len(self.starts) and self.starts[i] == offset * 8:
            return i
        return None

    def _trusted(self) -> int:
        """How many tokens from the start are read whole: every one that ends
        on a byte at least ``margin`` bits before the cut, and the tokens
        before it."""
        cut = self.end * 8 - self.margin
        i = bisect_right(self.ends, cut)
        while i > 0 and self.ends[i - 1] % 8:
            i -= 1
        return i

    def extend(self, stop: int, decode) -> None:
        """Have the tokens reach ``stop``: decode the rest, from the last
        trusted boundary — or, where no boundary is a fresh start, from the
        origin."""
        if stop <= self.end:
            return
        kept = self._trusted() if self.resumable else 0
        resume = self.ends[kept - 1] // 8 if kept else self.origin
        del self.starts[kept:], self.ends[kept:], self.texts[kept:]
        del self.byte_starts[kept:], self.byte_ends[kept:], self.chars[kept + 1 :]
        run: RunResult = decode(self.data[resume:stop], self.tables)
        self._append(resume, run.tokens)
        self.end = stop

    def _append(self, base: int, tokens: list[Token]) -> None:
        """Tokens relative to byte ``base``, after those kept."""
        base *= 8
        at = self.chars[-1]
        for token in tokens:
            start, end = base + token.bit_start, base + token.bit_end
            text = token.text()
            self.starts.append(start)
            self.ends.append(end)
            self.byte_starts.append(start // 8)
            self.byte_ends.append(max(start // 8 + 1, -(-end // 8)))
            self.texts.append(text)
            at += len(text)
            self.chars.append(at)

    def prepend(self, start: int, tokens: list[Token]) -> bool:
        """Put tokens decoded from byte ``start`` up to the origin in front of
        those kept, when they join: the last ends where the first kept token
        starts, and decoding on from there reads the same. ``True`` when they
        were taken."""
        if not self.resumable or start >= self.origin or not tokens:
            return False
        if start * 8 + tokens[-1].bit_end != self.origin * 8:
            return False
        kept = (self.starts, self.ends, self.byte_starts, self.byte_ends, self.texts)
        self.starts, self.ends, self.byte_starts, self.byte_ends, self.texts = (
            [] for _ in kept
        )
        self.chars = [0]
        self._append(start, tokens)
        for column, rest in zip(
            (self.starts, self.ends, self.byte_starts, self.byte_ends, self.texts),
            kept,
            strict=True,
        ):
            column.extend(rest)
        at = self.chars[-1]
        for text in kept[4]:
            at += len(text)
            self.chars.append(at)
        self.origin = start
        return True

    def above(self, lo: int, offset: int) -> TextModel | None:
        """The kept tokens from the first starting at or after byte ``lo`` to
        those ending by ``offset``: the text above a window there, when the
        tokens reach that far back; ``None`` when they do not."""
        if lo < self.origin or offset > self.end:
            return None
        first = bisect_left(self.starts, lo * 8)
        last = bisect_right(self.ends, offset * 8)
        if last <= first:
            return None
        return self._model(first, last, self.byte_starts[first], offset)

    def model(self, offset: int, stop: int) -> TextModel | None:
        """The tokens from ``offset`` that end by ``stop``, as a model; ``None``
        when they cannot be served from here."""
        first = self._first(offset)
        if first is None or stop > self.end:
            return None
        return self._model(first, bisect_right(self.ends, stop * 8), offset, stop)

    def _model(self, first: int, last: int, offset: int, stop: int) -> TextModel:
        """Tokens ``first`` to ``last`` as the text of bytes ``offset`` to
        ``stop``."""
        base = self.chars[first]
        char_starts = [c - base for c in self.chars[first:last]]
        char_ends = [c - base for c in self.chars[first + 1 : last + 1]]
        byte_starts, byte_ends = (
            self.byte_starts[first:last],
            self.byte_ends[first:last],
        )
        spans = list(zip(char_starts, char_ends, byte_starts, byte_ends, strict=True))
        columns = (char_starts, char_ends, byte_starts, byte_ends)
        body = "".join(self.texts[first:last])
        return TextModel(body, spans, offset, stop - offset, columns)


class TextWidget(QWidget):
    selection_changed = Signal(int, int)
    """Absolute byte range selected, end exclusive; (-1, -1) for none."""
    fit_changed = Signal()
    """The room for text changed — the box was resized, its font changed or
    Wrap was switched — so a different window fits it now."""
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
        self.wrap = QCheckBox("Wrap")
        self.wrap.setToolTip(
            "Fold long lines to the window's width.\n"
            "Off, a line ends only where a token's line break says it does."
        )
        self.wrap.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.note = QLabel("")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.note, 1)
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
        stored = settings().value(WORD_WRAP_KEY, "true")
        self.wrap.setChecked(str(stored).lower() == "true")
        self.wrap.toggled.connect(self._on_wrap)
        self._apply_wrap(self.wrap.isChecked())
        self.edit.selectionChanged.connect(self._on_selection)
        self.edit.cursorPositionChanged.connect(self._on_selection)
        self._reported: tuple[int, int] | None = None

    def _on_wrap(self, on: bool) -> None:
        settings().setValue(WORD_WRAP_KEY, "true" if on else "false")
        self._apply_wrap(on)

    def _apply_wrap(self, on: bool) -> None:
        self.edit.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
            if on
            else QTextOption.WrapMode.NoWrap
        )
        # Unwrapped, a line wider than the box scrolls sideways, on a bar that
        # is there whether or not one is — a bar that came and went would
        # change the room for lines with the content.
        self.edit.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if on
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
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
                    self.scroll_requested.emit(-delta // 120 * 3)
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
