"""Text drawn into a cell it may not fit, condensed, cut and notched.

The trickiest thing the raw view does, and the one thing it does that is about
the *text* rather than the bytes: a dictionary word or a long code name may be
three times its byte's cell wide, and it still has to read, stay inside its
cell, and say that there is more. Paired with :mod:`mapchar.ui.marks`, whose
notch is what says so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QStaticText

if TYPE_CHECKING:
    from PySide6.QtGui import QFont, QPainter

MIN_SQUEEZE = 0.7
"""How far text wider than its cells is condensed before it is cut short, and
how far one character with nothing left to drop goes before the notch says so."""


class CellText:
    """Draws texts into cells in one pair of faces, remembering what it measured.

    A paint measures every token on screen, so both the measurements and the
    laid-out texts are kept: ``label`` picks the smaller face a bracketed name
    is drawn in, and is part of every key.
    """

    def __init__(self, font: QFont, label_font: QFont) -> None:
        self.font = font
        self.label_font = label_font
        self._measured: dict[tuple[bool, str], tuple[float, float]] = {}
        """What a text's advance and ink measure in the face, by whether it is
        a label — measured once, since a paint measures every token shown."""
        self._laid: dict[tuple[bool, str], QStaticText] = {}
        """Each text drawn whole, laid out once in its face."""

    def clear(self) -> None:
        """Forget both caches: the face is about to be measured again."""
        self._measured.clear()
        self._laid.clear()

    def lay_out(self, text: str, label: bool) -> QStaticText:
        """``text`` laid out in its face, ready to be placed."""
        laid = QStaticText(text)
        laid.setTextFormat(Qt.TextFormat.PlainText)
        laid.prepare(font=self.label_font if label else self.font)
        return laid

    def measure(self, painter: QPainter, text: str, label: bool) -> tuple[float, float]:
        """``text``'s advance and its ink's width in the painter's face."""
        key = (label, text)
        measured = self._measured.get(key)
        if measured is None:
            metrics = painter.fontMetrics()
            advance = metrics.horizontalAdvance(text)
            measured = (advance, max(advance, metrics.boundingRect(text).width()))
            self._measured[key] = measured
        return measured

    def fit(
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
        advance, drawn = self.measure(painter, text, label)
        if drawn <= room:
            # Whole, it is drawn from its layout, centred as drawText would.
            laid = self._laid.get((label, text))
            if laid is None:
                laid = self._laid[(label, text)] = self.lay_out(text, label)
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
        advance, drawn = self.measure(painter, text, label)
        while keep > 1 and drawn * MIN_SQUEEZE > room:
            keep -= 1
            text = whole[:keep]
            advance, drawn = self.measure(painter, text, label)
        while keep < len(whole):
            more = self.measure(painter, whole[: keep + 1], label)
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


__all__ = ["MIN_SQUEEZE", "CellText"]
