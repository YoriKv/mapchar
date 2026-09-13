"""The icon set as a font: one bundled face, glyphs rasterized on demand.

Icons are drawn from ``resources/fonts/material-symbols-subset.ttf`` rather
than shipped as bitmaps, so every size is crisp on every display scale. The
face is a variable font drawn at weight 300, which reads as light strokes next
to the text views rather than as heavy furniture. What comes back is a mask,
the glyph as ink on transparency, stamped with a palette color so it tracks
the theme; a caller re-bakes its icons when the theme or the device scale
changes. Glyphs are fitted by their ink, not their font metrics, so a wide
mark fills its box the way a bitmap would and is centred on the mark.

The face is registered from bytes, since a frozen build has no path to open,
and needs a live QApplication, so registration happens on the first icon.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
)

from mapchar import resources
from mapchar.ui.glyphs import Glyph

_FONT_FILE = ("fonts", "material-symbols-subset.ttf")
_WEIGHT = 300.0
# How much of its box a glyph may fill; the rest keeps an antialiased edge off
# the boundary pixel, which would read as a cut-off icon.
_FILL = 0.86
_FIT_PASSES = 5

_family: str | None = None


def icon_font_family() -> str | None:
    """The registered icon-font family, or ``None`` if it could not load."""
    global _family
    if _family is None:
        font_id = QFontDatabase.addApplicationFontFromData(
            QByteArray(resources.read_bytes(*_FONT_FILE))
        )
        families = QFontDatabase.applicationFontFamilies(font_id)
        _family = families[0] if families else None
    return _family


def stamped(mask: QPixmap, color: QColor) -> QPixmap:
    """A copy of ``mask`` with ``color`` stamped through its alpha."""
    pixmap = mask.copy()
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)
    painter.end()
    return pixmap


def glyph_mask(glyph: Glyph, box: QSize) -> QPixmap:
    """``glyph`` as white ink on transparency, fitted and centred in ``box``
    (device pixels)."""
    mask = QPixmap(box)
    mask.fill(Qt.GlobalColor.transparent)
    family = icon_font_family()
    if family is None or box.width() <= 0 or box.height() <= 0:
        return mask
    room = QSize(round(box.width() * _FILL), round(box.height() * _FILL))
    drawn = _fitted_ink(family, glyph.value, room)
    if drawn is None:
        return mask
    painter = QPainter(mask)
    painter.drawImage(
        (box.width() - drawn.width()) // 2,
        (box.height() - drawn.height()) // 2,
        drawn,
    )
    painter.end()
    return mask


def _fitted_ink(family: str, text: str, room: QSize) -> QImage | None:
    """``text`` rasterized as large as fits ``room``, cropped to its ink.

    Converges rather than solving: the rasterizer rounds outlines onto the
    pixel grid, so each pass renders, measures and rescales by the shortfall.
    """
    size = max(1, room.height())
    best: QImage | None = None
    for _ in range(_FIT_PASSES):
        drawn = _render(family, text, size)
        if drawn is None:
            return None
        scale = min(room.width() / drawn.width(), room.height() / drawn.height())
        if drawn.width() <= room.width() and drawn.height() <= room.height():
            best = drawn
            grown = max(1, int(size * scale))
            if grown <= size:
                break
            size = grown
        else:
            size = max(1, min(int(size * scale), size - 1))
    return best if best is not None else drawn


def _render(family: str, text: str, size: int) -> QImage | None:
    """``text`` at ``size``, cropped to the pixels it inked."""
    scratch = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32_Premultiplied)
    scratch.fill(Qt.GlobalColor.transparent)
    painter = QPainter(scratch)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(QColor(Qt.GlobalColor.white))
    painter.setFont(_icon_font(family, size))
    painter.drawText(QPointF(size * 0.5, size * 1.5), text)
    painter.end()
    bounds = _ink_bounds(scratch)
    return None if bounds is None else scratch.copy(bounds)


def _ink_bounds(image: QImage) -> QRect | None:
    """The tightest rectangle holding every non-transparent pixel."""
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    top, bottom, left, right = None, None, alpha.width(), -1
    for y in range(alpha.height()):
        row = bytes(alpha.constScanLine(y))[: alpha.width()]
        if not any(row):
            continue
        top = y if top is None else top
        bottom = y
        left = min(left, next(x for x, a in enumerate(row) if a))
        right = max(
            right, len(row) - 1 - next(x for x, a in enumerate(reversed(row)) if a)
        )
    if top is None:
        return None
    return QRect(left, top, right - left + 1, bottom - top + 1)


def _icon_font(family: str, pixel_size: int) -> QFont:
    font = QFont(family)
    font.setPixelSize(pixel_size)
    font.setVariableAxis(QFont.Tag("wght"), _WEIGHT)
    return font


def glyph_pixmap(glyph: Glyph, color: QColor, box: QSize, ratio: float) -> QPixmap:
    """``glyph`` in ``color``; ``box`` in logical units rendered at ``ratio``."""
    mask = glyph_mask(
        glyph, QSize(round(box.width() * ratio), round(box.height() * ratio))
    )
    tinted = stamped(mask, color)
    tinted.setDevicePixelRatio(ratio)
    return tinted


def glyph_icon(
    glyph: Glyph, color: QColor, size: int = 16, ratio: float = 1.0
) -> QIcon:
    """``glyph`` as a square :class:`QIcon` in ``color``, for a button's face.

    16 is the icon size the styles give a button that never asked for one. The
    art is baked, so a caller re-bakes it when the theme or scale changes.
    """
    return QIcon(glyph_pixmap(glyph, color, QSize(size, size), ratio))
