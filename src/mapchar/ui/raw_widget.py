"""The raw view: rows of address · hex · decoded text, aligned by byte."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QPainter,
    QPen,
    QStaticText,
)
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip, QWidget

from mapchar.core.table import TokenKind
from mapchar.core.tokens import Token
from mapchar.ui import BYTES_PER_ROW, marks, theme
from mapchar.ui.token_text import (
    POINTER_TOKENS,
    display_text,
    row_runs,
    token_bytes,
)
from mapchar.ui.widgets import mono_font, wheel_steps

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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
    bounds: tuple[int, int] | None = None
    """The absolute byte range the view is confined to, which is what the
    scrollbar spans; the whole buffer when ``None``."""
    tips: dict[int, str] = field(default_factory=dict)
    """A token's hover text in place of its own, by its first bit: what a
    pointer holds and reaches."""


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
        self._font = mono_font()
        self._label_font = QFont(self._font)
        self._label_font.setPointSize(8)
        self._metrics_key: tuple[str, float] | None = None
        self._metrics = QFontMetrics(self._font, self.viewport())
        self._char_width = 1
        self._pair_width = 2.0
        self._hex_x = self._text_x = self._text_w = 0
        self._lefts: list[int] = []
        """The x of each hex cell in a row."""
        self._pairs: list[QStaticText] = []
        """Every byte's hex pair, laid out once for the face."""
        self._measured: dict[tuple[bool, str], tuple[float, float]] = {}
        """What a text's advance and ink measure in the face, by whether it is
        a label — measured once, since a paint measures every token shown."""
        self._laid: dict[tuple[bool, str], QStaticText] = {}
        """Each text drawn whole, laid out once in its face."""
        self._row_height = 1
        self._display: list[tuple[str, bool]] = []
        self._tints: list[QColor | None] = []
        """Each token's text and tint, worked out as the model is set rather
        than at every paint."""
        self._sel: tuple[int, int] | None = None
        self._bits: tuple[int, int] | None = None
        self._structure: tuple[int, int] | None = None
        """The absolute bit range selected when the selection is tokens picked in
        the text column, ``_sel`` then being the bytes it touches; ``None`` when
        the selection is whole bytes."""
        self._anchor: int | None = None
        self._anchor_bits: tuple[int, int] | None = None
        self._address_digits = 6
        self._addr_of: Callable[[int], str] | None = None
        self._addr_width = 0
        """How the address column spells an offset and how wide it is; ``None``
        spells flat hex as wide as the buffer needs."""
        self._base = 0
        """The byte the scrollbar's first row starts at: the bounds' start."""
        self._token_of: list[Token | None] = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        # The rows are a fixed width; a view narrower than them scrolls sideways
        # rather than cutting the text column off.
        self.horizontalScrollBar().valueChanged.connect(
            lambda _: self.viewport().update()
        )
        self._syncing = False
        self._wheel_rest_x = self._wheel_rest_y = 0
        """What a wheel turned short of a notch, kept for the next turn."""
        self._sync_metrics()

    # --- geometry ------------------------------------------------------

    def _sync_metrics(self) -> None:
        """Re-measure the face against the surface it will be drawn on.

        A ``QFontMetrics`` made without a paint device answers for the primary
        screen, so a window on a differently scaled monitor lays its cells out
        to one width and draws them at another — and text measured as fitting
        is then sliced at the cell's edge. Measured once per face and scale:
        everything a paint places is placed from these numbers, and asking the
        face for them cell by cell is most of a paint.
        """
        key = (self._font.key(), self.viewport().devicePixelRatioF())
        if key == self._metrics_key:
            return
        self._metrics_key = key
        self._metrics = QFontMetrics(self._font, self.viewport())
        self._row_height = self._metrics.height() + 4
        self._char_width = self._metrics.horizontalAdvance("0")
        self._pair_width = QFontMetricsF(self._font, self.viewport()).horizontalAdvance(
            "00"
        )
        self._measured.clear()
        self._laid.clear()
        self._pairs = [self._lay_out(f"{byte:02X}", False) for byte in range(256)]
        self._place_columns()

    def _lay_out(self, text: str, label: bool) -> QStaticText:
        """``text`` laid out in its face, ready to be placed."""
        laid = QStaticText(text)
        laid.setTextFormat(Qt.TextFormat.PlainText)
        laid.prepare(font=self._label_font if label else self._font)
        return laid

    def _place_columns(self) -> None:
        """Lay the columns out from the face and the address width."""
        cw = self._char_width
        self._hex_x = (self._address_chars + 2) * cw
        width, gap = HEX_CELL * cw, self._group_gap
        self._lefts = [
            self._hex_x + c * width + c // HEX_GROUP * gap for c in range(BYTES_PER_ROW)
        ]
        hex_w = BYTES_PER_ROW * width + (BYTES_PER_ROW - 1) // HEX_GROUP * gap
        self._text_x = self._hex_x + hex_w + 2 * cw
        self._text_w = BYTES_PER_ROW * self._text_width

    @property
    def _address_chars(self) -> int:
        """How many characters the address column holds."""
        if self._addr_of is None:
            return self._address_digits
        return self._addr_width

    def set_address_format(
        self, addr_of: Callable[[int], str] | None, width: int = 0
    ) -> None:
        """Spell the address column with ``addr_of``, in a column ``width``
        characters wide — the window's one ``AddressSpelling``, so the column
        reads as every other address the window shows. ``None`` goes back to
        flat hex, as wide as the buffer needs."""
        self._addr_of, self._addr_width = addr_of, width
        self._place_columns()
        self._sync_horizontal()
        self.viewport().update()

    def _address_text(self, offset: int) -> str:
        if self._addr_of is None:
            return f"{offset:0{self._address_digits}X}"
        return self._addr_of(offset)

    @property
    def row_height(self) -> int:
        return self._row_height

    @property
    def char_width(self) -> int:
        return self._char_width

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
        return self._hex_x, self._text_x, self._text_w

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
        width = HEX_CELL * self._char_width
        left = self._lefts[col]
        return QRect(
            left,
            row * self.row_height,
            self._lefts[col + span - 1] + width - left,
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
        start = max(token.bit_start, 0)
        end = token.bit_end if token.bit_end > start else start + 8
        return self._text_span(start, min(end, limit * 8))

    def _row_spans(self, start: int, end: int) -> Iterator[tuple[int, int, int]]:
        """A relative bit range as ``(row, first, stop)`` pieces, one per row,
        with ``first`` and ``stop`` counted from the row's first bit."""
        row_bits = BYTES_PER_ROW * 8
        while start < end:
            row = start // row_bits
            stop = min(end, (row + 1) * row_bits)
            yield row, start - row * row_bits, stop - row * row_bits
            start = stop

    def _text_span(self, start: int, end: int) -> list[QRectF]:
        """The text column over a relative bit range, one rect per row."""
        text_x, width = self._columns()[1], self._text_width
        return [
            QRectF(
                text_x + first / 8 * width,
                row * self.row_height,
                (stop - first) / 8 * width,
                self.row_height,
            )
            for row, first, stop in self._row_spans(start, end)
        ]

    def _hex_span(self, start: int, end: int) -> list[QRectF]:
        """The hex column over a relative bit range, one rect per row.

        An edge on a byte boundary is its cell's own edge, so whole bytes read
        as the byte selection does. An edge inside a byte falls inside the pair
        at the bit it splits: each digit is a nibble, so a code over the last
        two bits of one byte and the first four of the next covers half a digit
        and then a whole one.
        """
        pair = self._pair_width

        def edge(bit: int, closing: bool) -> float:
            byte, inner = divmod(bit, 8)
            if inner == 0:
                cell = self._hex_cell(byte - 1 if closing else byte)
                return cell.left() + (cell.width() if closing else 0)
            cell = self._hex_cell(byte)
            return cell.left() + (cell.width() - pair) / 2 + inner / 8 * pair

        spans = []
        for row, first, stop in self._row_spans(start, end):
            bit = row * BYTES_PER_ROW * 8
            left, right = edge(bit + first, False), edge(bit + stop, True)
            spans.append(
                QRectF(left, row * self.row_height, right - left, self.row_height)
            )
        return spans

    # --- model ---------------------------------------------------------

    def set_model(self, model: RowModel | None) -> None:
        self._model = model
        self._token_of = []
        self._display = []
        self._tints = []
        if model is not None:
            self._token_of = [None] * len(model.data)
            for token in model.tokens:
                for rel in token_bytes(token):
                    if rel < len(model.data) and self._token_of[rel] is None:
                        self._token_of[rel] = token
            self._display = [display_text(t) for t in model.tokens]
            self._tints = [self._tint(t) for t in model.tokens]
            digits = max(4, len(f"{max(model.total - 1, 0):X}"))
            if digits != self._address_digits:
                self._address_digits = digits
                self._place_columns()
            # The bar's rows are counted from the bounds' start, so a view
            # confined to one string scrolls over that string and no further.
            self._base, end = model.bounds or (0, model.total)
            total_rows = -(-(end - self._base) // BYTES_PER_ROW)
            self._syncing = True
            sb = self.verticalScrollBar()
            sb.setRange(0, max(0, total_rows - self.visible_rows))
            sb.setPageStep(self.visible_rows)
            sb.setValue((model.offset - self._base) // BYTES_PER_ROW)
            self._syncing = False
        self._sync_horizontal()
        self.viewport().update()

    def set_selection(self, start: int, end: int) -> None:
        """Select bytes on somebody else's behalf — a search hit, a string the
        Files panel opened, the Text tab's own selection.

        This is where a Shift+click then reaches from: the anchor moves to the
        selection's start, and an empty selection leaves none. The widget's own
        picking and extending do not come through here, so a drag keeps the
        anchor it started at.
        """
        self._sel = (start, end) if end > start else None
        self._bits = None
        self._anchor = self._sel[0] if self._sel is not None else None
        self._anchor_bits = None
        self.viewport().update()

    def clear_anchor(self) -> None:
        """Forget where a Shift+click reaches from: the bytes under the anchor
        are not the ones on screen any more — another entry, other bounds, or
        another payload in the Decompressed view."""
        self._anchor = self._anchor_bits = None

    def selection(self) -> tuple[int, int] | None:
        return self._sel

    def set_structure(self, span: tuple[int, int] | None) -> None:
        """Wash the absolute bytes ``span`` covers, or none: the compressed
        structure the Decompressed view is reading, so where it sits in the
        file and how far it runs shows in the file itself."""
        if span != self._structure:
            self._structure = span
            self.viewport().update()

    def structure(self) -> tuple[int, int] | None:
        return self._structure

    def selection_bits(self) -> tuple[int, int] | None:
        """The absolute bit range of a selection of tokens; ``None`` for bytes."""
        return self._bits

    def _on_scroll(self, value: int) -> None:
        if not self._syncing:
            self.offset_requested.emit(self._base + value * BYTES_PER_ROW)

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

        # The compressed structure on preview, under everything said of its
        # bytes: they are still tokens, pointers and a selection.
        if self._structure is not None:
            s, e = self._structure
            lo, hi = max(s - model.offset, 0), min(e - model.offset, limit)
            for first, last in row_runs(range(lo, hi)):
                span = last - first + 1
                painter.fillRect(self._hex_cell(first, span), theme.TINT_STRUCTURE)
                painter.fillRect(self._text_cell(first, span), theme.TINT_STRUCTURE)

        # Token tints: one chip per token per row, so where one ends reads.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for token, color, (face, _label) in zip(
            model.tokens, self._tints, self._display, strict=True
        ):
            if color is None:
                continue
            covered = [r for r in token_bytes(token) if r < limit]
            for first, last in row_runs(covered):
                marks.chip(painter, self._hex_cell(first, last - first + 1), color)
            # Unmatched data is chipped in the hex column only: its dot already
            # says it in the text column, and chips there are noise.
            # A token that shows nothing — a table switch, a return — is a
            # tick where it sits rather than an empty chip.
            if token.entry is not None or token.fallback:
                for segment in self._text_segments(token, limit):
                    if face:
                        marks.chip(painter, segment, color)
                    else:
                        marks.tick(painter, segment, color)
        pointers = [r for r in model.pointer_bytes if 0 <= r < len(model.data)]
        for first, last in row_runs(r for r in pointers if r < shown):
            marks.chip(
                painter, self._hex_cell(first, last - first + 1), theme.TINT_POINTER
            )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Selection: tokens by their bits, straddling bytes as they do, or
        # whole runs of cells.
        if self._bits is not None:
            base = model.offset * 8
            lo = max(self._bits[0] - base, 0)
            hi = min(self._bits[1] - base, limit * 8)
            for rect in self._hex_span(lo, hi) + self._text_span(lo, hi):
                painter.fillRect(rect, theme.TINT_SELECTION)
        elif self._sel is not None:
            s, e = self._sel
            lo, hi = max(s - model.offset, 0), min(e - model.offset, len(model.data))
            for first, last in row_runs(range(lo, min(hi, shown))):
                span = last - first + 1
                painter.fillRect(self._hex_cell(first, span), theme.TINT_SELECTION)
                painter.fillRect(self._text_cell(first, span), theme.TINT_SELECTION)

        # String boundary rules, in both columns.
        for rel in model.string_starts:
            if 0 <= rel < min(shown, len(model.data)):
                for cell in (self._hex_cell(rel), self._text_cell(rel)):
                    marks.rule(painter, cell)

        # Addresses and hex, each pair centred in its own cell: the pairs are
        # laid out once for the face, and only placed here.
        painter.setFont(self._font)
        pair_size = self._pairs[0].size()
        cell_w = HEX_CELL * cw
        dx, dy = (cell_w - pair_size.width()) / 2, (rh - pair_size.height()) / 2
        for row in range(rows):
            start = row * BYTES_PER_ROW
            painter.setPen(QPen(dim))
            painter.drawText(
                QRect(cw, row * rh, self._address_chars * cw + cw, rh),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._address_text(model.offset + start),
            )
            painter.setPen(QPen(ink))
            top = row * rh + dy
            for col, byte in enumerate(model.data[start : start + BYTES_PER_ROW]):
                painter.drawStaticText(
                    QPointF(self._lefts[col] + dx, top), self._pairs[byte]
                )

        # Decoded text, each token inside the cells of its own first row.
        label_ink = QColor(ink)
        label_ink.setAlpha(200)
        cut_marks: list[QRectF] = []
        for token, (text, is_label) in zip(model.tokens, self._display, strict=True):
            segments = self._text_segments(token, limit) if text else []
            if not segments:
                continue
            # A token broken over a row end is written where most of it is.
            cell = max(segments, key=QRectF.width)
            if is_label:
                painter.setFont(self._label_font)
                painter.setPen(QPen(label_ink))
            else:
                painter.setFont(self._font)
                painter.setPen(QPen(dim if token.entry is None else ink))
            if self._fit(painter, cell, text, is_label):
                cut_marks.append(cell)

        # A corner notch on every token shown cut short; the tooltip has it all.
        for cell in cut_marks:
            marks.notch(painter, cell, dim)

    def _measure(
        self, painter: QPainter, text: str, label: bool
    ) -> tuple[float, float]:
        """``text``'s advance and its ink's width in the painter's face."""
        key = (label, text)
        measured = self._measured.get(key)
        if measured is None:
            metrics = painter.fontMetrics()
            advance = metrics.horizontalAdvance(text)
            measured = (advance, max(advance, metrics.boundingRect(text).width()))
            self._measured[key] = measured
        return measured

    def _fit(
        self, painter: QPainter, cell: QRectF, text: str, label: bool = False
    ) -> bool:
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
        room = cell.width() - 2
        advance, drawn = self._measure(painter, text, label)
        if drawn <= room:
            # Whole, it is drawn from its layout, centred as drawText would.
            laid = self._laid.get((label, text))
            if laid is None:
                laid = self._laid[(label, text)] = self._lay_out(text, label)
            size = laid.size()
            painter.drawStaticText(
                QPointF(
                    cell.left() + (cell.width() - size.width()) / 2,
                    cell.top() + (cell.height() - size.height()) / 2,
                ),
                laid,
            )
            return False
        # As many characters as fit condensed: guessed from the width so far,
        # since the ink runs about even with the count, then settled a
        # character at a time from there rather than from the end of a preview
        # that runs to eighty.
        whole = text
        keep = max(
            1, min(len(whole) - 1, int(len(whole) * room / (drawn * MIN_SQUEEZE)))
        )
        text = whole[:keep]
        advance, drawn = self._measure(painter, text, label)
        while keep > 1 and drawn * MIN_SQUEEZE > room:
            keep -= 1
            text = whole[:keep]
            advance, drawn = self._measure(painter, text, label)
        while keep < len(whole):
            more = self._measure(painter, whole[: keep + 1], label)
            if more[1] * MIN_SQUEEZE > room:
                break
            keep += 1
            text, (advance, drawn) = whole[:keep], more
        cut = keep < len(whole)
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
        if token.table_id == POINTER_TOKENS:
            return theme.TINT_POINTER
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
        tip = model.tips.get(token.bit_start)
        if tip is not None:
            QToolTip.showText(event.globalPos(), f"{tip}\n{spelled}", self)
            return
        text = token.text().replace("\n", "↵") or "(nothing)"
        if token.entry is None and not token.fallback:
            text = "no match"
        where = f" · @{token.table_id}" if token.table_id else ""
        QToolTip.showText(event.globalPos(), f"{text}\n{spelled}{where}", self)

    def _token_bits_at(self, pos: QPoint) -> tuple[int, int] | None:
        """The absolute bits of the token under a point in the text column."""
        model = self._model
        if model is None or pos.x() < self._columns()[1]:
            return None
        token = self._token_at(pos)
        if token is None:
            return None
        start = model.offset * 8 + token.bit_start
        return start, start + max(token.bit_end - token.bit_start, 1)

    def mousePressEvent(self, event) -> None:
        pos = self._content_pos(event.position().toPoint())
        b = self._byte_at(pos)
        bits = self._token_bits_at(pos)
        if event.button() == Qt.MouseButton.RightButton:
            if b is not None and not (self._sel and self._sel[0] <= b < self._sel[1]):
                self._pick(b, bits)
            self.context_menu_requested.emit(event.globalPosition().toPoint())
            return
        if b is None:
            self._anchor = self._anchor_bits = None
            self._select(-1, -1)
            return
        # Shift takes the selection out to here from where the last click left
        # its anchor; with nothing anchored yet, it is that click.
        shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        if shift and self._anchor is not None:
            self._extend(b, bits)
            return
        self._pick(b, bits)

    def _pick(self, byte: int, bits: tuple[int, int] | None) -> None:
        """Select what a click lands on: the token under it in the text column,
        by its bits, or the byte under it in the hex column."""
        self._anchor, self._anchor_bits = byte, bits
        if bits is None:
            self._select(byte, byte + 1)
        else:
            self._select_bits(*bits)

    def _extend(self, byte: int | None, bits: tuple[int, int] | None) -> None:
        """Take the selection from the anchor out to here: what a drag and a
        Shift+click both do, each column in its own units — whole tokens by
        their bits in the text column, bytes in the hex column. A point that is
        not in the column the anchor was set in reaches nothing, and the
        selection stays as it was; without an anchor there is nothing to reach
        from at all. Neither end reaches past what the view holds.
        """
        if self._anchor is None:
            return
        first, stop = self._reach()
        if self._anchor_bits is not None:
            if bits is not None:
                lo = max(min(self._anchor_bits[0], bits[0]), first * 8)
                hi = min(max(self._anchor_bits[1], bits[1]), stop * 8)
                if hi > lo:
                    self._select_bits(lo, hi)
            return
        if byte is None:
            return
        lo = max(min(self._anchor, byte), first)
        hi = min(max(self._anchor, byte), stop - 1)
        if hi >= lo:
            self._select(lo, hi + 1)

    def _reach(self) -> tuple[int, int]:
        """The absolute bytes a selection may cover: the view's bounds, else
        the whole buffer."""
        model = self._model
        if model is None:
            return (0, 0)
        return model.bounds or (0, model.total)

    def mouseMoveEvent(self, event) -> None:
        if self._anchor is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        pos = self._content_pos(event.position().toPoint())
        self._extend(self._byte_at(pos), self._token_bits_at(pos))

    def _select(self, start: int, end: int) -> None:
        self._bits = None
        self._sel = (start, end) if end > start else None
        self.viewport().update()
        if self._sel is None:
            self.selection_changed.emit(-1, -1)
        else:
            self.selection_changed.emit(start, end)

    def _select_bits(self, start: int, end: int) -> None:
        """Select an absolute bit range; the bytes it touches are what the rest
        of the window is told."""
        first, stop = start // 8, -(-end // 8)
        self._sel = (first, stop)
        self._bits = (start, end)
        self.viewport().update()
        self.selection_changed.emit(first, stop)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta()
        if delta.x() and not delta.y():
            steps, self._wheel_rest_x = wheel_steps(self._wheel_rest_x, delta.x())
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - steps * bar.singleStep())
            return
        steps, self._wheel_rest_y = wheel_steps(self._wheel_rest_y, delta.y())
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() - steps * 3)
