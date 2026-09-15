"""The marks the byte views draw over their cells.

The raw view paints them over its bytes and the Legend paints them beside their
meanings, so both draw the same shapes from here: a rounded chip for a token's
tint, a tick where a token shows nothing, a rule where a string starts, and a
corner notch on text cut short.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from mapchar.ui import theme


def chip(painter: QPainter, cell: QRect | QRectF, color: QColor) -> None:
    """A token's tint: a rounded chip filling its cell."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(cell).adjusted(1, 1, -1, -1), 3, 3)


def tick(painter: QPainter, cell: QRect | QRectF, color: QColor) -> None:
    """A token that shows nothing — a table switch, a return: a narrow, more
    solid chip at its cell's left edge rather than an empty one."""
    strong = QColor(color)
    strong.setAlpha(220)
    box = QRectF(cell)
    chip(painter, QRectF(box.left() - 1, box.top(), 4, box.height()), strong)


def rule(painter: QPainter, cell: QRect | QRectF) -> None:
    """Where a string starts: a hairline down the cell's left edge."""
    box = QRectF(cell)
    painter.setPen(QPen(theme.TINT_STRING_RULE, 1))
    painter.drawLine(QPointF(box.left(), box.top()), QPointF(box.left(), box.bottom()))


def notch(painter: QPainter, cell: QRect | QRectF, color: QColor) -> None:
    """Text cut short: a corner notch at the cell's top right."""
    box = QRectF(cell)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    right, top = box.right(), box.top() + 1
    painter.drawPolygon(
        [QPointF(right - 4, top), QPointF(right, top), QPointF(right, top + 4)]
    )
