"""A font's glyph sheet: the PNG cut into cells, and the view that shows it.

:class:`GlyphSheet` draws a laid-out string into an image — what the Preview
tab shows — and :class:`GlyphSheetView` draws the sheet itself, which is where
an alphabet is picked.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from mapchar.core.font import Font, TextBox
from mapchar.engines.layout import Layout
from mapchar.ui import theme


class GlyphSheet:
    """A PNG cut into cells; draws glyphs into a QImage."""

    def __init__(self, font: Font):
        self.font = font
        self.image = QImage(font.path) if font.path else QImage()
        self.ok = not self.image.isNull()
        self.transparent: QColor | None = None
        if self.ok:
            if (
                font.transparent is not None
                and self.image.format() == QImage.Format.Format_Indexed8
            ):
                table = self.image.colorTable()
                if font.transparent < len(table):
                    self.transparent = QColor(table[font.transparent])
            elif self.ok:
                self.transparent = QColor(self.image.pixelColor(0, 0))

    def cell(self, glyph: int) -> QRect:
        col, row = glyph % self.font.columns, glyph // self.font.columns
        return QRect(
            col * self.font.cell_width,
            row * self.font.cell_height,
            self.font.cell_width,
            self.font.cell_height,
        )

    def measured_widths(self, gap: int = 1) -> tuple[int, ...]:
        """Advance per glyph: the last inked column plus a gap."""
        if not self.ok:
            return ()
        widths = []
        rows = self.image.height() // self.font.cell_height
        for glyph in range(rows * self.font.columns):
            rect = self.cell(glyph)
            last = -1
            for x in range(rect.width()):
                for y in range(rect.height()):
                    c = self.image.pixelColor(rect.x() + x, rect.y() + y)
                    if self.transparent is None or c != self.transparent:
                        last = x
                        break
            widths.append(last + 1 + gap if last >= 0 else self.font.cell_width // 2)
        return tuple(widths)

    def render(
        self,
        result: Layout,
        box: TextBox,
        page: int,
        scale: int,
        grid: bool = False,
    ) -> QImage:
        out = QImage(
            max(box.width, 1) * scale,
            max(box.height, 1) * scale,
            QImage.Format.Format_ARGB32,
        )
        out.fill(theme.PREVIEW_PAPER)
        painter = QPainter(out)
        for p in result.placements:
            if p.page != page or p.glyph is None:
                if p.page == page and p.glyph is None:
                    painter.setPen(QPen(theme.ERROR_INK))
                    painter.drawRect(
                        QRect(
                            p.x * scale,
                            p.y * scale,
                            self.font.cell_width * scale - 1,
                            self.font.cell_height * scale - 1,
                        )
                    )
                continue
            src = self.cell(p.glyph)
            if self.ok:
                tile = self.image.copy(src).convertToFormat(QImage.Format.Format_ARGB32)
                if self.transparent is not None:
                    for y in range(tile.height()):
                        for x in range(tile.width()):
                            if QColor(tile.pixelColor(x, y)) == self.transparent:
                                tile.setPixelColor(x, y, QColor(0, 0, 0, 0))
                dest = QRect(
                    p.x * scale, p.y * scale, src.width() * scale, src.height() * scale
                )
                painter.drawImage(dest, tile)
            if p.overflow:
                painter.fillRect(
                    QRect(
                        p.x * scale,
                        p.y * scale,
                        src.width() * scale,
                        src.height() * scale,
                    ),
                    theme.TINT_END,
                )
        if grid and scale >= 3:
            painter.setPen(QPen(theme.PREVIEW_GRID))
            for x in range(0, box.width + 1):
                painter.drawLine(x * scale, 0, x * scale, box.height * scale)
            for y in range(0, box.height + 1):
                painter.drawLine(0, y * scale, box.width * scale, y * scale)
        painter.end()
        return out


class GlyphSheetView(QWidget):
    """The sheet as a grid of cells, each captioned with what it spells.

    Clicking picks one tile, or a whole row when ``rows`` is on; the pick is
    where Fill lays its characters and what Shift moves.
    """

    picked = Signal(int, int)
    """The first and last glyph index of the pick."""

    CAPTION = 14
    """Pixels under each cell for its character."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._font: Font | None = None
        self._sheet: GlyphSheet | None = None
        self._spelling: dict[int, str] = {}
        self._rows = 0
        self.scale = 3
        self.rows = False
        self.first = 0
        self.last = 0

    def set_font(self, font: Font | None, sheet: GlyphSheet | None) -> None:
        self._font, self._sheet = font, sheet
        self._spelling = {}
        if font is not None:
            for i, ch in enumerate(font.units):
                self._spelling[font.base + i] = ch
            for text, glyph in font.glyphs.items():
                self._spelling[glyph] = text
        self._rows = 0
        if font is not None and sheet is not None and sheet.ok:
            self._rows = max(1, sheet.image.height() // max(font.cell_height, 1))
        elif font is not None:
            highest = max(self._spelling, default=0)
            self._rows = highest // max(font.columns, 1) + 1
        self.updateGeometry()
        self.resize(self.sizeHint())
        self.update()

    # --- geometry ---------------------------------------------------------

    def _cell_size(self) -> tuple[int, int]:
        f = self._font or Font(None)
        return f.cell_width * self.scale, f.cell_height * self.scale + self.CAPTION

    def sizeHint(self):
        f = self._font or Font(None)
        w, h = self._cell_size()
        return QSize(w * f.columns + 1, h * max(self._rows, 1) + 1)

    def minimumSizeHint(self):
        return self.sizeHint()

    def glyph_at(self, x: int, y: int) -> int | None:
        f = self._font
        if f is None:
            return None
        w, h = self._cell_size()
        col, row = x // w, y // h
        if col < 0 or col >= f.columns or row < 0 or row >= self._rows:
            return None
        return row * f.columns + col

    def set_pick(self, first: int, last: int | None = None) -> None:
        self.first = first
        self.last = first if last is None else last
        self.update()
        self.picked.emit(self.first, self.last)

    def mousePressEvent(self, event) -> None:
        glyph = self.glyph_at(int(event.position().x()), int(event.position().y()))
        if glyph is None:
            return
        f = self._font
        if self.rows and f is not None:
            start = glyph - glyph % f.columns
            self.set_pick(start, start + f.columns - 1)
        else:
            self.set_pick(glyph)

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        f = self._font
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if f is None:
            painter.end()
            return
        w, h = self._cell_size()
        tile_h = f.cell_height * self.scale
        highlight = self.palette().highlight().color()
        grid = QColor(highlight)
        grid.setAlpha(60)
        for glyph in range(self._rows * f.columns):
            col, row = glyph % f.columns, glyph // f.columns
            x, y = col * w, row * h
            if self._sheet is not None and self._sheet.ok:
                src = self._sheet.cell(glyph)
                painter.drawImage(QRect(x, y, w, tile_h), self._sheet.image, src)
            painter.setPen(QPen(grid))
            painter.drawRect(QRect(x, y, w, tile_h))
            text = self._spelling.get(glyph, "")
            if text:
                painter.setPen(QPen(self.palette().text().color()))
                painter.drawText(
                    QRect(x, y + tile_h, w, self.CAPTION),
                    Qt.AlignmentFlag.AlignCenter,
                    text if len(text) <= 3 else text[:2] + "…",
                )
            if self.first <= glyph <= self.last:
                painter.setPen(QPen(highlight, 2))
                painter.drawRect(QRect(x + 1, y + 1, w - 2, h - 2))
        painter.end()


__all__ = ["GlyphSheet", "GlyphSheetView"]
