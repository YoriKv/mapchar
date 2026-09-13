"""The bundled icon font and the two themes, both of which fail silently:
a glyph the shipped face lacks draws nothing, and a light palette that
followed a dark desktop still looks like a theme."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import glyph_mask, icon_font_family
from mapchar.ui.theme import palette_for

_BOXES = (QSize(13, 16), QSize(16, 16))


def _ink(pixmap) -> int:
    image = pixmap.toImage()
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    )


def test_every_glyph_is_a_single_codepoint(qapp) -> None:
    for glyph in Glyph:
        assert len(glyph.value) == 1, f"{glyph.name} is {glyph.value!r}"


def test_the_icon_font_loads_and_every_glyph_draws(qapp) -> None:
    assert icon_font_family() is not None
    for glyph in Glyph:
        for box in _BOXES:
            mask = glyph_mask(glyph, box)
            assert mask.size() == box
            assert _ink(mask) > 0, f"{glyph.name} drew nothing at {box.width()}px"


def test_light_is_light_and_dark_is_dark(qapp) -> None:
    style = QStyleFactory.create("Fusion")
    light = palette_for("light", style).color(QPalette.ColorRole.Window)
    dark = palette_for("dark", style).color(QPalette.ColorRole.Window)
    assert light.lightness() > 160
    assert dark.lightness() < 96
    assert isinstance(QApplication.instance(), QApplication)
