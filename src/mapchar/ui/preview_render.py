"""Drawing a laid-out string: the image the Preview tab shows.

The layout engine places every character in box pixels; this draws them in the
preview font, on the Preview's own paper. The painter is scaled rather than the
image, so a zoomed page keeps crisp text and every position stays the box
coordinate the engine gave it.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QImage, QPainter, QPen

from mapchar.core.font import Font, TextBox
from mapchar.engines.layout import Layout
from mapchar.ui import theme


def render(
    result: Layout,
    font: Font,
    box: TextBox,
    qfont: QFont,
    page: int,
    scale: int,
    grid: bool = False,
) -> QImage:
    """One page of ``result``, drawn at ``scale`` pixels per box pixel."""
    out = QImage(
        max(box.width, 1) * scale,
        max(box.height, 1) * scale,
        QImage.Format.Format_ARGB32,
    )
    out.fill(theme.PREVIEW_PAPER)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(scale, scale)
    painter.setFont(qfont)
    for p in result.placements:
        if p.page != page:
            continue
        width = font.advance(p.text)
        cell = QRectF(p.x, p.y, width, font.height)
        if p.missing:
            # Nothing to draw: a box says as much, as the Hex view's · does.
            painter.setPen(QPen(theme.ERROR_INK, 1 / scale))
            painter.drawRect(cell.adjusted(0.5, 0.5, -0.5, -0.5))
        else:
            painter.setPen(QPen(theme.PREVIEW_INK))
            painter.drawText(
                QRectF(p.x, p.y, width, font.height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                p.text,
            )
        if p.overflow:
            painter.fillRect(cell, theme.TINT_END)
    painter.resetTransform()
    if grid and scale >= 3:
        painter.setPen(QPen(theme.PREVIEW_GRID))
        for x in range(box.width + 1):
            painter.drawLine(x * scale, 0, x * scale, box.height * scale)
        for y in range(box.height + 1):
            painter.drawLine(0, y * scale, box.width * scale, y * scale)
    painter.end()
    return out


__all__ = ["render"]
