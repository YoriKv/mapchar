"""The raw view: rows of address · hex · decoded text, aligned by byte."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip, QWidget

from mapchar.core.table import TokenKind
from mapchar.core.tokens import Token
from mapchar.ui import BYTES_PER_ROW, theme

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

TEXT_FAMILIES = (
    "Monospace",
    "DejaVu Sans Mono",
    "Noto Sans Mono CJK JP",
    "Noto Sans CJK JP",
    "MS Gothic",
    "Yu Gothic",
    "Meiryo",
    "Hiragino Sans",
    "monospace",
)
"""Families in fallback order: a monospaced face for the hex, then faces that
draw kana and kanji, so a Japanese decode is not a row of boxes."""

HEX_CELL = 3
"""Character widths a byte owns in the hex column: its pair and a space."""
TEXT_CELL = 2.5
"""Character widths a byte owns in the text column: a full-width glyph, with
room for two of them squeezed, or a short code name."""
HEX_GROUP = 4
"""Bytes between the small gaps that make a hex row countable."""
MIN_SQUEEZE = 0.7
"""How far text wider than its cells is condensed before it is cut short, and
how far one character with nothing left to drop goes before the notch says so."""


@dataclass
class RowModel:
    """What the widget paints: a byte window and the tokens over it."""

    offset: int
    """Byte offset of the first row."""
    data: bytes
    """The bytes of the window."""
    tokens: list[Token]
    """Tokens with bit positions relative to ``offset * 8``."""
    string_starts: set[int]
    """Byte offsets (relative) where the current block starts a string."""
    total: int
    """Total bytes in the buffer, for the scrollbar."""
    pointer_bytes: set[int] = field(default_factory=set)
    """Relative byte offsets that hold the current block's pointers."""


def token_bytes(token: Token) -> range:
    """The relative bytes a token covers, at least the one it starts in."""
    first = token.bit_start // 8
    return range(first, max(first, (token.bit_end - 1) // 8) + 1)


def row_runs(rels: Iterable[int]) -> Iterator[tuple[int, int]]:
    """Sorted byte offsets as ``(first, last)`` runs, broken at row ends."""
    run: list[int] = []
    for rel in sorted(rels):
        if run and (rel != run[1] + 1 or rel % BYTES_PER_ROW == 0):
            yield run[0], run[1]
            run = []
        run = [run[0] if run else rel, rel]
    if run:
        yield run[0], run[1]


BREAK_MARK = "↵"
CODE_MARK = "▪"
_EMBEDDED_BREAK = re.compile(r"\[[^\[\]]*\]\n|\n")
_EMBEDDED_CODE = re.compile(r"\[[^\[\]]*\]")


def display_text(token: Token) -> tuple[str, bool]:
    """What the text column shows for a token, and whether it is a label.

    Unmatched data is a dot (the hex column already shows its bytes). A token
    whose whole text is one bracketed name — a code, an end token, a table
    entry written as ``[tile60]`` — shows the name alone, as a label. Text with
    a name inside it keeps its letters: a name ending a line becomes
    :data:`BREAK_MARK` and any other :data:`CODE_MARK`, so ``s[line]`` reads
    ``s↵``; the tooltip has it whole.
    """
    if token.entry is None and not token.fallback:
        return "·", False
    text = token.text()
    bare = text.strip("\n")
    if bare.startswith("[") and bare.endswith("]") and bare.count("[") == 1:
        return bare[1:-1], True
    text = _EMBEDDED_BREAK.sub(BREAK_MARK, text)
    return _EMBEDDED_CODE.sub(CODE_MARK, text), False


class RawWidget(QAbstractScrollArea):
    offset_requested = Signal(int)
    """The user scrolled: show this byte offset at the top."""
    selection_changed = Signal(int, int)
    """Absolute byte range [start, end) selected, end exclusive; (-1, -1) none."""
    context_menu_requested = Signal(QPoint)
    rows_changed = Signal()
    """The view has room for a different number of rows than it had: whoever
    feeds it a model may want to hand it a window of the new size."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._model: RowModel | None = None
        self._rows_shown = 0
        self._font = QFont()
        self._font.setFamilies(TEXT_FAMILIES)
        self._font.setStyleHint(QFont.StyleHint.TypeWriter)
        self._font.setPointSize(10)
        self._label_font = QFont(self._font)
        self._label_font.setPointSize(8)
        self._metrics = QFontMetrics(self._font, self.viewport())
        self._sel: tuple[int, int] | None = None
        self._anchor: int | None = None
        self._address_digits = 6
        self._token_of: list[Token | None] = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        # The rows are a fixed width; a view narrower than them scrolls sideways
        # rather than cutting the text column off.
        self.horizontalScrollBar().valueChanged.connect(
            lambda _: self.viewport().update()
        )
        self._syncing = False

    # --- geometry ------------------------------------------------------

    def _sync_metrics(self) -> None:
        """Re-measure the face against the surface it will be drawn on.

        A ``QFontMetrics`` made without a paint device answers for the primary
        screen, so a window on a differently scaled monitor lays its cells out
        to one width and draws them at another — and text measured as fitting
        is then sliced at the cell's edge.
        """
        self._metrics = QFontMetrics(self._font, self.viewport())

    @property
    def row_height(self) -> int:
        return self._metrics.height() + 4

    @property
    def char_width(self) -> int:
        return self._metrics.horizontalAdvance("0")

    @property
    def visible_rows(self) -> int:
        return max(1, self.viewport().height() // self.row_height)

    def visible_bytes(self) -> int:
        return self.visible_rows * BYTES_PER_ROW

    @property
    def _group_gap(self) -> int:
        return max(3, self.char_width // 2)

    @property
    def _text_width(self) -> int:
        return int(TEXT_CELL * self.char_width)

    def _columns(self) -> tuple[int, int, int]:
        """x of the hex column, x of the text column, text column width."""
        cw = self.char_width
        hex_x = (self._address_digits + 2) * cw
        hex_w = BYTES_PER_ROW * HEX_CELL * cw
        hex_w += (BYTES_PER_ROW - 1) // HEX_GROUP * self._group_gap
        text_x = hex_x + hex_w + 2 * cw
        return hex_x, text_x, BYTES_PER_ROW * self._text_width

    def content_width(self) -> int:
        """How wide the rows are drawn: address, hex, text and a margin."""
        _, text_x, text_w = self._columns()
        return text_x + text_w + self.char_width

    def _sync_horizontal(self) -> None:
        bar = self.horizontalScrollBar()
        room = self.viewport().width()
        bar.setRange(0, max(0, self.content_width() - room))
        bar.setPageStep(room)
        bar.setSingleStep(self.char_width * 2)

    def _content_pos(self, pos: QPoint) -> QPoint:
        """A viewport point in row coordinates, past any sideways scroll."""
        return QPoint(pos.x() + self.horizontalScrollBar().value(), pos.y())

    def _hex_cell(self, rel: int, span: int = 1) -> QRect:
        """The hex cells of ``span`` bytes from ``rel``, gaps between included.

        A byte owns a fixed cell, and whatever is drawn for it — tint, hex
        pair — is placed in that rect rather than advanced to by the font,
        whose true character width is fractional.
        """
        row, col = divmod(rel, BYTES_PER_ROW)
        width = HEX_CELL * self.char_width
        hex_x = self._columns()[0]

        def left(c: int) -> int:
            return hex_x + c * width + c // HEX_GROUP * self._group_gap

        last = col + span - 1
        return QRect(
            left(col),
            row * self.row_height,
            left(last) + width - left(col),
            self.row_height,
        )

    def _text_cell(self, rel: int, span: int = 1) -> QRect:
        """The text cells of ``span`` bytes from ``rel``."""
        row, col = divmod(rel, BYTES_PER_ROW)
        width = self._text_width
        return QRect(
            self._columns()[1] + col * width,
            row * self.row_height,
            span * width,
            self.row_height,
        )

    def _text_segments(self, token: Token, limit: int) -> list[QRectF]:
        """Where a token sits in the text column, one rect per row it touches.

        Placed by **bit**, not by byte: a table of 6-bit codes starts several
        tokens inside one byte, and each still gets a place of its own — three
        quarters of a byte cell — rather than all of them sharing the cell.
        A byte-aligned token is exactly its byte cells. ``limit`` is the byte
        count painted; nothing past it is placed.
        """
        row_bits = BYTES_PER_ROW * 8
        start = max(token.bit_start, 0)
        end = token.bit_end if token.bit_end > start else start + 8
        end = min(end, limit * 8)
        text_x, width = self._columns()[1], self._text_width
        segments = []
        while start < end:
            row = start // row_bits
            stop = min(end, (row + 1) * row_bits)
            left = text_x + (start - row * row_bits) / 8 * width
            right = text_x + (stop - row * row_bits) / 8 * width
            segments.append(
                QRectF(left, row * self.row_height, right - left, self.row_height)
            )
            start = stop
        return segments

    # --- model ---------------------------------------------------------

    def set_model(self, model: RowModel | None) -> None:
        self._model = model
        self._token_of = []
        if model is not None:
            self._token_of = [None] * len(model.data)
            for token in model.tokens:
                for rel in token_bytes(token):
                    if rel < len(model.data) and self._token_of[rel] is None:
                        self._token_of[rel] = token
            self._address_digits = max(4, len(f"{max(model.total - 1, 0):X}"))
            total_rows = -(-model.total // BYTES_PER_ROW)
            self._syncing = True
            sb = self.verticalScrollBar()
            sb.setRange(0, max(0, total_rows - self.visible_rows))
            sb.setPageStep(self.visible_rows)
            sb.setValue(model.offset // BYTES_PER_ROW)
            self._syncing = False
        self._sync_horizontal()
        self.viewport().update()

    def set_selection(self, start: int, end: int) -> None:
        self._sel = (start, end) if end > start else None
        self.viewport().update()

    def selection(self) -> tuple[int, int] | None:
        return self._sel

    def _on_scroll(self, value: int) -> None:
        if not self._syncing:
            self.offset_requested.emit(value * BYTES_PER_ROW)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_metrics()
        if self._model is not None:
            self.set_model(self._model)
        else:
            self._sync_horizontal()
        if self.visible_rows != self._rows_shown:
            self._rows_shown = self.visible_rows
            self.rows_changed.emit()

    # --- painting ------------------------------------------------------

    def paintEvent(self, event) -> None:
        self._sync_metrics()
        painter = QPainter(self.viewport())
        pal = self.palette()
        painter.fillRect(self.viewport().rect(), pal.base())
        model = self._model
        if model is None:
            return
        hex_x, text_x, text_w = self._columns()
        cw, rh = self.char_width, self.row_height
        rows = min(self.visible_rows + 1, -(-len(model.data) // BYTES_PER_ROW))
        shown = rows * BYTES_PER_ROW
        limit = min(shown, len(model.data))
        ink = pal.text().color()
        dim = QColor(ink)
        dim.setAlpha(140)
        faint = QColor(ink)
        faint.setAlpha(40)
        scrolled = self.horizontalScrollBar().value()
        painter.translate(-scrolled, 0)
        width = max(self.viewport().width() + scrolled, self.content_width())

        # Every other row banded, so a row can be followed from hex to text.
        for row in range(1, rows, 2):
            painter.fillRect(0, row * rh, width, rh, pal.alternateBase())
        painter.setPen(QPen(faint, 1))
        painter.drawLine(text_x - cw, 0, text_x - cw, rows * rh)

        # Token tints: one chip per token per row, so where one ends reads.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for token in model.tokens:
            color = self._tint(token)
            if color is None:
                continue
            covered = [r for r in token_bytes(token) if r < limit]
            for first, last in row_runs(covered):
                self._chip(painter, self._hex_cell(first, last - first + 1), color)
            # Unmatched data is chipped in the hex column only: its dot already
            # says it in the text column, and chips there are noise.
            # A token that shows nothing — a table switch, a return — is a
            # tick where it sits rather than an empty chip.
            if token.entry is not None or token.fallback:
                tick = not display_text(token)[0]
                strong = QColor(color)
                strong.setAlpha(220)
                for segment in self._text_segments(token, limit):
                    if tick:
                        segment = QRectF(segment.left() - 1, segment.top(), 4, rh)
                    self._chip(painter, segment, strong if tick else color)
        pointers = [r for r in model.pointer_bytes if 0 <= r < len(model.data)]
        for first, last in row_runs(r for r in pointers if r < shown):
            self._chip(
                painter, self._hex_cell(first, last - first + 1), theme.TINT_POINTER
            )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Selection, as whole runs of cells.
        if self._sel is not None:
            s, e = self._sel
            lo, hi = max(s - model.offset, 0), min(e - model.offset, len(model.data))
            for first, last in row_runs(range(lo, min(hi, shown))):
                span = last - first + 1
                painter.fillRect(self._hex_cell(first, span), theme.TINT_SELECTION)
                painter.fillRect(self._text_cell(first, span), theme.TINT_SELECTION)

        # String boundary rules, in both columns.
        painter.setPen(QPen(theme.TINT_STRING_RULE, 1))
        for rel in model.string_starts:
            if 0 <= rel < min(shown, len(model.data)):
                for cell in (self._hex_cell(rel), self._text_cell(rel)):
                    painter.drawLine(
                        cell.left(), cell.top(), cell.left(), cell.bottom()
                    )

        # Addresses and hex, each pair centred in its own cell.
        painter.setFont(self._font)
        center = Qt.AlignmentFlag.AlignCenter
        for row in range(rows):
            start = row * BYTES_PER_ROW
            painter.setPen(QPen(dim))
            painter.drawText(
                QRect(cw, row * rh, self._address_digits * cw + cw, rh),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{model.offset + start:0{self._address_digits}X}",
            )
            painter.setPen(QPen(ink))
            for rel in range(start, min(start + BYTES_PER_ROW, len(model.data))):
                painter.drawText(self._hex_cell(rel), center, f"{model.data[rel]:02X}")

        # Decoded text, each token inside the cells of its own first row.
        label_ink = QColor(ink)
        label_ink.setAlpha(200)
        cut_marks: list[QRectF] = []
        for token in model.tokens:
            text, is_label = display_text(token)
            segments = self._text_segments(token, limit) if text else []
            if not segments:
                continue
            # A token broken over a row end is written where most of it is.
            cell = max(segments, key=QRectF.width)
            if is_label:
                painter.setFont(self._label_font)
                painter.setPen(QPen(label_ink))
                cut = self._fit(painter, cell, text)
            else:
                painter.setFont(self._font)
                painter.setPen(QPen(dim if token.entry is None else ink))
                cut = self._fit(painter, cell, text)
            if cut:
                cut_marks.append(cell)

        # A corner notch on every token shown cut short; the tooltip has it all.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dim)
        for cell in cut_marks:
            right, top = cell.right(), cell.top() + 1
            painter.drawPolygon(
                [
                    QPointF(right - 4, top),
                    QPointF(right, top),
                    QPointF(right, top + 4),
                ]
            )

    @staticmethod
    def _chip(painter: QPainter, cell: QRect | QRectF, color: QColor) -> None:
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(cell).adjusted(1, 1, -1, -1), 3, 3)

    @staticmethod
    def _fit(painter: QPainter, cell: QRectF, text: str) -> bool:
        """Draw ``text`` centred in ``cell``, never past it; ``True`` if cut short.

        Text wider than the cell — a dictionary word on one byte, a long code
        name — is condensed down to :data:`MIN_SQUEEZE`, and past that only as
        many characters as fit are drawn, so it never covers the token beside
        it. One character that is wider than its cell even condensed that far
        is condensed the rest of the way instead: a glyph narrower than it
        should be still reads, and one sliced down the middle by the cell's
        edge does not.

        What is measured is what will be drawn: the painter's own metrics, which
        answer for the face and the surface actually drawing, and the ink rather
        than the advance, since a glyph's bearings can carry it past the width
        the advance claims. Measured anywhere else, or by the advance alone,
        text is called narrow enough to fit and then drawn wider than its cell.
        """
        metrics = painter.fontMetrics()
        room = cell.width() - 2
        advance = metrics.horizontalAdvance(text)
        drawn = max(advance, metrics.boundingRect(text).width())
        if drawn <= room:
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, text)
            return False
        cut = False
        while len(text) > 1 and drawn * MIN_SQUEEZE > room:
            text = text[:-1]
            advance = metrics.horizontalAdvance(text)
            drawn = max(advance, metrics.boundingRect(text).width())
            cut = True
        squeeze = min(1.0, room / drawn) if drawn > 0 else 1.0
        painter.save()
        painter.setClipRect(cell)
        painter.translate(cell.left() + cell.width() / 2, cell.top())
        painter.scale(squeeze, 1)
        painter.drawText(
            QRectF(-advance / 2 - 1, 0, advance + 2, cell.height()),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )
        painter.restore()
        # Condensed past what is meant to be legible is as much a warning that
        # the tooltip has more as a character dropped is.
        return cut or squeeze < MIN_SQUEEZE

    @staticmethod
    def _tint(token: Token) -> QColor | None:
        if token.fallback:
            return theme.TINT_SWITCH
        if token.entry is None:
            return theme.TINT_RAW
        kind = token.entry.kind
        if kind is TokenKind.END:
            return theme.TINT_END
        if kind in (TokenKind.SWITCH, TokenKind.RETURN):
            return theme.TINT_SWITCH
        if kind is TokenKind.CODE:
            return theme.TINT_CODE
        return None

    # --- input ---------------------------------------------------------

    def _byte_at(self, pos: QPoint) -> int | None:
        model = self._model
        if model is None:
            return None
        hex_x, text_x, text_w = self._columns()
        cw = self.char_width
        row = pos.y() // self.row_height
        x = pos.x()
        if hex_x <= x < text_x - cw:
            # A gap between groups belongs to the byte before it.
            group = HEX_GROUP * HEX_CELL * cw + self._group_gap
            index, inside = divmod(x - hex_x, group)
            col = index * HEX_GROUP + min(inside // (HEX_CELL * cw), HEX_GROUP - 1)
            if col >= BYTES_PER_ROW:
                return None
        elif text_x <= x < text_x + text_w:
            col = (x - text_x) // self._text_width
        else:
            return None
        rel = row * BYTES_PER_ROW + int(col)
        if rel >= len(model.data):
            return None
        return model.offset + rel

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            self._show_tooltip(event)
            return True
        return super().viewportEvent(event)

    def _token_at(self, pos: QPoint) -> Token | None:
        """The token under a point: by bit in the text column, where tokens are
        placed by bit, and the first over the byte in the hex column."""
        b = self._byte_at(pos)
        model = self._model
        if b is None or model is None:
            return None
        _, text_x, _ = self._columns()
        if pos.x() < text_x:
            return self._token_of[b - model.offset]
        row = pos.y() // self.row_height
        bit = row * BYTES_PER_ROW * 8 + int((pos.x() - text_x) * 8 / self._text_width)
        return next(
            (
                t
                for t in model.tokens
                if t.bit_start <= bit < max(t.bit_end, t.bit_start + 1)
            ),
            self._token_of[b - model.offset],
        )

    def _show_tooltip(self, event) -> None:
        """The token under the pointer, whole: its text, its bytes, its table."""
        token = self._token_at(self._content_pos(event.pos()))
        model = self._model
        if token is None:
            QToolTip.hideText()
            event.ignore()
            return
        rels = [r for r in token_bytes(token) if r < len(model.data)]
        spelled = " ".join(f"{model.data[r]:02X}" for r in rels)
        text = token.text().replace("\n", "↵") or "(nothing)"
        if token.entry is None and not token.fallback:
            text = "no match"
        where = f" · @{token.table_id}" if token.table_id else ""
        QToolTip.showText(event.globalPos(), f"{text}\n{spelled}{where}", self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            b = self._byte_at(self._content_pos(event.position().toPoint()))
            if b is not None and not (self._sel and self._sel[0] <= b < self._sel[1]):
                self._select(b, b + 1)
            self.context_menu_requested.emit(event.globalPosition().toPoint())
            return
        b = self._byte_at(self._content_pos(event.position().toPoint()))
        if b is None:
            self._anchor = None
            self._select(-1, -1)
            return
        self._anchor = b
        self._select(b, b + 1)

    def mouseMoveEvent(self, event) -> None:
        if self._anchor is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        b = self._byte_at(self._content_pos(event.position().toPoint()))
        if b is None:
            return
        lo, hi = min(self._anchor, b), max(self._anchor, b)
        self._select(lo, hi + 1)

    def _select(self, start: int, end: int) -> None:
        self._sel = (start, end) if end > start else None
        self.viewport().update()
        if self._sel is None:
            self.selection_changed.emit(-1, -1)
        else:
            self.selection_changed.emit(start, end)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta()
        if delta.x() and not delta.y():
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta.x() // 120 * bar.singleStep())
            return
        steps = -delta.y() // 120
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() + steps * 3)
