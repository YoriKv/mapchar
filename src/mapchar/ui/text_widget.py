"""The text mode of the central view: decoded text as an ordinary text box.

Presentation only, on the pattern of celPix's text window: it holds a body
and a map from characters to bytes, never decodes anything itself, and
reports selections as byte ranges so the aligned view and the status bar can
follow. Read-only until the Strings view's editor exists.

The box holds a window sized to itself, like the raw view's rows: it says how
much of a body is in view (:meth:`TextWidget.fitted_chars`), and whoever feeds
it cuts the window to that. So it never scrolls on its own — the wheel and the
page keys move the view instead — and its scrollbars are fixed, since one that
came and went with the content would change the room, and with it the window,
and with it the content.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.tokens import Token
from mapchar.ui import settings

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


def text_model(tokens: list[Token], offset: int, length: int) -> TextModel:
    """Render tokens (bit positions relative to ``offset``) to a body and map."""
    parts: list[str] = []
    spans: list[tuple[int, int, int, int]] = []
    at = 0
    for token in tokens:
        text = token.text()
        byte_start = offset + token.bit_start // 8
        byte_end = offset + max(byte_start - offset + 1, -(-token.bit_end // 8))
        spans.append((at, at + len(text), byte_start, byte_end))
        parts.append(text)
        at += len(text)
    return TextModel("".join(parts), spans, offset, length)


class TextWidget(QWidget):
    selection_changed = Signal(int, int)
    """Absolute byte range selected, end exclusive; (-1, -1) for none."""
    fit_changed = Signal()
    """The room for text changed — the box was resized, its font changed or
    Wrap was switched — so a different window fits it now."""
    scroll_requested = Signal(int)
    """The wheel turned over the box: move the view by this many lines, down
    for positive."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._model: TextModel | None = None
        self._syncing = False
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.edit.setUndoRedoEnabled(False)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit.installEventFilter(self)
        self.edit.viewport().installEventFilter(self)
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
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
        elif watched is self.edit and kind == QEvent.Type.FontChange:
            self.fit_changed.emit()
        return super().eventFilter(watched, event)

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

    def _char_to_byte(self, pos: int) -> int | None:
        model = self._model
        if model is None:
            return None
        for cs, ce, bs, _be in model.spans:
            if cs <= pos < ce or (cs == ce == pos):
                return bs
        if model.spans and pos >= model.spans[-1][1]:
            return model.spans[-1][3]
        return None

    def _on_selection(self) -> None:
        if self._syncing or self._model is None:
            return
        cursor = self.edit.textCursor()
        a, b = sorted((cursor.anchor(), cursor.position()))
        if a == b:
            return
        start = self._char_to_byte(a)
        end = None
        for cs, ce, _bs, be in self._model.spans:
            if cs < b <= ce:
                end = be
                break
        if start is None or end is None or end <= start:
            return
        if self._reported == (start, end):
            return
        self._reported = (start, end)
        self.selection_changed.emit(start, end)

    def select_bytes(self, start: int, end: int) -> None:
        """Highlight the characters whose bytes fall in ``[start, end)``."""
        model = self._model
        if model is None:
            return
        chars = [(cs, ce) for cs, ce, bs, be in model.spans if bs < end and be > start]
        self._syncing = True
        try:
            cursor = self.edit.textCursor()
            if not chars:
                cursor.clearSelection()
            else:
                cursor.setPosition(chars[0][0])
                cursor.setPosition(chars[-1][1], QTextCursor.MoveMode.KeepAnchor)
            self.edit.setTextCursor(cursor)
            self._reported = (start, end)
        finally:
            self._syncing = False
