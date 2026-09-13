"""The raw view: rows of address · hex · decoded text, aligned by byte."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from mapchar.core.table import EntryKind
from mapchar.core.tokens import Token
from mapchar.ui import theme

BYTES_PER_ROW = 16


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


class RawWidget(QAbstractScrollArea):
    offset_requested = Signal(int)
    """The user scrolled: show this byte offset at the top."""
    selection_changed = Signal(int, int)
    """Absolute byte range [start, end) selected, end exclusive; (-1, -1) none."""
    context_menu_requested = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._model: RowModel | None = None
        self._font = QFont("Monospace")
        self._font.setStyleHint(QFont.StyleHint.TypeWriter)
        self._font.setPointSize(10)
        self._metrics = QFontMetrics(self._font)
        self._sel: tuple[int, int] | None = None
        self._anchor: int | None = None
        self._address_digits = 6
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._syncing = False

    # --- geometry ------------------------------------------------------

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

    def _columns(self) -> tuple[int, int, int]:
        """x of the hex column, x of the text column, text column width."""
        cw = self.char_width
        hex_x = (self._address_digits + 2) * cw
        text_x = hex_x + BYTES_PER_ROW * 3 * cw + cw
        return hex_x, text_x, BYTES_PER_ROW * 3 * cw

    # --- model ---------------------------------------------------------

    def set_model(self, model: RowModel | None) -> None:
        self._model = model
        if model is not None:
            self._address_digits = max(4, len(f"{max(model.total - 1, 0):X}"))
            total_rows = -(-model.total // BYTES_PER_ROW)
            self._syncing = True
            sb = self.verticalScrollBar()
            sb.setRange(0, max(0, total_rows - self.visible_rows))
            sb.setPageStep(self.visible_rows)
            sb.setValue(model.offset // BYTES_PER_ROW)
            self._syncing = False
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
        if self._model is not None:
            self.set_model(self._model)

    # --- painting ------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.setFont(self._font)
        pal = self.palette()
        painter.fillRect(self.viewport().rect(), pal.base())
        model = self._model
        if model is None:
            return
        hex_x, text_x, text_w = self._columns()
        cw, rh = self.char_width, self.row_height
        ascent = self._metrics.ascent() + 2
        rows = self.visible_rows + 1
        dim = QColor(pal.text().color())
        dim.setAlpha(140)

        # Token tints under both columns.
        for token in model.tokens:
            first = token.bit_start // 8
            last = max(first, (token.bit_end - 1) // 8)
            color = self._tint(token)
            if color is None:
                continue
            for b in range(first, last + 1):
                if b >= len(model.data):
                    break
                row, col = divmod(b, BYTES_PER_ROW)
                if row >= rows:
                    break
                y = row * rh
                painter.fillRect(QRect(hex_x + col * 3 * cw, y, 3 * cw, rh), color)
                painter.fillRect(QRect(text_x + col * 3 * cw, y, 3 * cw, rh), color)

        # Selection.
        if self._sel is not None:
            s, e = self._sel
            for b in range(
                max(s - model.offset, 0), min(e - model.offset, len(model.data))
            ):
                row, col = divmod(b, BYTES_PER_ROW)
                y = row * rh
                painter.fillRect(
                    QRect(hex_x + col * 3 * cw, y, 3 * cw, rh), theme.TINT_SELECTION
                )
                painter.fillRect(
                    QRect(text_x + col * 3 * cw, y, 3 * cw, rh), theme.TINT_SELECTION
                )

        # Addresses and hex.
        painter.setPen(QPen(pal.text().color()))
        for row in range(rows):
            start = row * BYTES_PER_ROW
            if start >= len(model.data):
                break
            y = row * rh + ascent
            painter.setPen(QPen(dim))
            painter.drawText(cw, y, f"{model.offset + start:0{self._address_digits}X}")
            painter.setPen(QPen(pal.text().color()))
            chunk = model.data[start : start + BYTES_PER_ROW]
            painter.drawText(hex_x, y, " ".join(f"{b:02X}" for b in chunk))

        # String boundary rules.
        painter.setPen(QPen(theme.TINT_STRING_RULE, 1))
        for rel in model.string_starts:
            if 0 <= rel < len(model.data):
                row, col = divmod(rel, BYTES_PER_ROW)
                if row < rows:
                    x = text_x + col * 3 * cw - cw // 2
                    painter.drawLine(x, row * rh, x, row * rh + rh)

        # Decoded text, each token under its first byte, clipped to its span.
        painter.setPen(QPen(pal.text().color()))
        for token in model.tokens:
            first = token.bit_start // 8
            if first >= len(model.data):
                continue
            span_bytes = max(1, -(-(token.bit_end - token.bit_start) // 8))
            row, col = divmod(first, BYTES_PER_ROW)
            if row >= rows:
                continue
            if token.entry is None and not token.fallback:
                # The hex column already shows the byte; a dot marks "no match".
                text = "·" * span_bytes
            else:
                text = token.text().replace("\n", "↵")
            if not text:
                continue
            x = text_x + col * 3 * cw
            width = min(span_bytes, BYTES_PER_ROW - col) * 3 * cw - cw // 2
            painter.save()
            painter.setClipRect(QRect(x, row * rh, width, rh))
            painter.drawText(x, row * rh + ascent, text)
            painter.restore()

    @staticmethod
    def _tint(token: Token) -> QColor | None:
        if token.fallback:
            return theme.TINT_SWITCH
        if token.entry is None:
            return theme.TINT_RAW
        kind = token.entry.kind
        if kind is EntryKind.END:
            return theme.TINT_END
        if kind in (EntryKind.SWITCH, EntryKind.RETURN):
            return theme.TINT_SWITCH
        if kind is EntryKind.CODE:
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
        if hex_x <= x < hex_x + BYTES_PER_ROW * 3 * cw:
            col = (x - hex_x) // (3 * cw)
        elif text_x <= x < text_x + text_w:
            col = (x - text_x) // (3 * cw)
        else:
            return None
        rel = row * BYTES_PER_ROW + int(col)
        if rel >= len(model.data):
            return None
        return model.offset + rel

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            b = self._byte_at(event.position().toPoint())
            if b is not None and not (self._sel and self._sel[0] <= b < self._sel[1]):
                self._select(b, b + 1)
            self.context_menu_requested.emit(event.globalPosition().toPoint())
            return
        b = self._byte_at(event.position().toPoint())
        if b is None:
            self._anchor = None
            self._select(-1, -1)
            return
        self._anchor = b
        self._select(b, b + 1)

    def mouseMoveEvent(self, event) -> None:
        if self._anchor is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        b = self._byte_at(event.position().toPoint())
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
        steps = -event.angleDelta().y() // 120
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() + steps * 3)
