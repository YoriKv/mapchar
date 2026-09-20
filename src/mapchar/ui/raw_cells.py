"""The raw view's rows: what it paints, and where every cell of a row sits.

The arithmetic behind :mod:`mapchar.ui.raw_widget`, with none of the widget in
it: a row is an address, sixteen hex cells and sixteen text cells, placed from
the face's measurements and the address column's width. A byte owns a cell and
whatever is drawn for it is placed in that cell, rather than advanced to by the
font — whose true character width is fractional, so advancing would drift a
row's right-hand end away from the row above it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QRectF

from mapchar.ui import BYTES_PER_ROW

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PySide6.QtGui import QFontMetrics

    from mapchar.core.tokens import Token

HEX_CELL = 3
"""Character widths a byte owns in the hex column: its pair and a space."""
TEXT_CELL = 2.5
"""Character widths a byte owns in the text column: a full-width glyph, with
room for two of them squeezed, or a short code name."""
HEX_GROUP = 4
"""Bytes between the small gaps that make a hex row countable."""


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


class CellGeometry:
    """Where every cell of a row sits, for one face and one address width.

    Built afresh whenever either moves — the face is re-measured against the
    surface it draws on, or the address column changes width — so everything a
    paint places comes from one set of numbers rather than from the face, cell
    by cell.
    """

    def __init__(
        self, metrics: QFontMetrics, pair_width: float, address_chars: int
    ) -> None:
        self.row_height = metrics.height() + 4
        self.char_width = metrics.horizontalAdvance("0")
        self.pair_width = pair_width
        """What a hex pair advances, fractional: the width a bit edge inside a
        byte is measured against."""
        self.address_chars = address_chars
        self.group_gap = max(3, self.char_width // 2)
        self.text_width = int(TEXT_CELL * self.char_width)
        cw = self.char_width
        self.hex_x = (address_chars + 2) * cw
        width = HEX_CELL * cw
        self.lefts = [
            self.hex_x + c * width + c // HEX_GROUP * self.group_gap
            for c in range(BYTES_PER_ROW)
        ]
        """The x of each hex cell in a row."""
        hex_w = (
            BYTES_PER_ROW * width + (BYTES_PER_ROW - 1) // HEX_GROUP * self.group_gap
        )
        self.text_x = self.hex_x + hex_w + 2 * cw
        self.text_w = BYTES_PER_ROW * self.text_width

    def columns(self) -> tuple[int, int, int]:
        """x of the hex column, x of the text column, text column width."""
        return self.hex_x, self.text_x, self.text_w

    def content_width(self) -> int:
        """How wide the rows are drawn: address, hex, text and a margin."""
        return self.text_x + self.text_w + self.char_width

    def hex_cell(self, rel: int, span: int = 1) -> QRect:
        """The hex cells of ``span`` bytes from ``rel``, gaps between included.

        A byte owns a fixed cell, and whatever is drawn for it — tint, hex
        pair — is placed in that rect rather than advanced to by the font,
        whose true character width is fractional.
        """
        row, col = divmod(rel, BYTES_PER_ROW)
        width = HEX_CELL * self.char_width
        left = self.lefts[col]
        return QRect(
            left,
            row * self.row_height,
            self.lefts[col + span - 1] + width - left,
            self.row_height,
        )

    def text_cell(self, rel: int, span: int = 1) -> QRect:
        """The text cells of ``span`` bytes from ``rel``."""
        row, col = divmod(rel, BYTES_PER_ROW)
        width = self.text_width
        return QRect(
            self.text_x + col * width,
            row * self.row_height,
            span * width,
            self.row_height,
        )

    def row_spans(self, start: int, end: int) -> Iterator[tuple[int, int, int]]:
        """A relative bit range as ``(row, first, stop)`` pieces, one per row,
        with ``first`` and ``stop`` counted from the row's first bit."""
        row_bits = BYTES_PER_ROW * 8
        while start < end:
            row = start // row_bits
            stop = min(end, (row + 1) * row_bits)
            yield row, start - row * row_bits, stop - row * row_bits
            start = stop

    def text_span(self, start: int, end: int) -> list[QRectF]:
        """The text column over a relative bit range, one rect per row."""
        text_x, width = self.text_x, self.text_width
        return [
            QRectF(
                text_x + first / 8 * width,
                row * self.row_height,
                (stop - first) / 8 * width,
                self.row_height,
            )
            for row, first, stop in self.row_spans(start, end)
        ]

    def hex_span(self, start: int, end: int) -> list[QRectF]:
        """The hex column over a relative bit range, one rect per row.

        An edge on a byte boundary is its cell's own edge, so whole bytes read
        as the byte selection does. An edge inside a byte falls inside the pair
        at the bit it splits: each digit is a nibble, so a code over the last
        two bits of one byte and the first four of the next covers half a digit
        and then a whole one.
        """
        pair = self.pair_width

        def edge(bit: int, closing: bool) -> float:
            byte, inner = divmod(bit, 8)
            if inner == 0:
                cell = self.hex_cell(byte - 1 if closing else byte)
                return cell.left() + (cell.width() if closing else 0)
            cell = self.hex_cell(byte)
            return cell.left() + (cell.width() - pair) / 2 + inner / 8 * pair

        spans = []
        for row, first, stop in self.row_spans(start, end):
            bit = row * BYTES_PER_ROW * 8
            left, right = edge(bit + first, False), edge(bit + stop, True)
            spans.append(
                QRectF(left, row * self.row_height, right - left, self.row_height)
            )
        return spans


__all__ = ["HEX_CELL", "HEX_GROUP", "TEXT_CELL", "CellGeometry", "RowModel"]
