"""The app-wide light/dark appearance: one palette on Fusion, never stylesheets.

Both themes run on **Fusion**, which honours the application palette everywhere
(the native Windows and macOS styles paint many controls from platform colors
and ignore it). ``setColorScheme`` is requested alongside for the parts a
palette cannot reach, most visibly the Windows title bar, and because Fusion's
``standardPalette()`` follows the platform's scheme: without pinning the scheme
to light first, the light theme comes out as the desktop's dark palette on a
dark desktop.

A theme is a data row (:class:`_PaletteSpec`): a surface color the whole
palette is derived from, plus the roles whose derived value is wrong. Light
names no surface, because Fusion's own standard palette is already a tuned
light palette. Dark derives from one seed; ``QPalette(QColor)`` computes
window, button, text and the bevel shades from it.

The palette is installed **before** the style: Qt propagates an application
palette through the event loop, and installing a style in between re-polishes
every widget against the palette it already has, so the queued PaletteChange
never arrives at the widgets that bake a palette color into a pixmap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory

if TYPE_CHECKING:
    from collections.abc import Mapping

THEMES = ("light", "dark")

# Colours that must read the same in both themes.
TINT_END = QColor(220, 80, 80, 90)
TINT_CODE = QColor(80, 140, 220, 90)
TINT_SWITCH = QColor(200, 140, 40, 90)
TINT_RAW = QColor(128, 128, 128, 70)
TINT_POINTER = QColor(90, 200, 120, 110)
TINT_SELECTION = QColor(60, 140, 240, 110)
TINT_STRING_RULE = QColor(120, 120, 120, 160)
# Warning and error inks sit at the lightness that reads equally on both
# themes' surfaces.
WARNING_INK = QColor(0xAE, 0x7A, 0x11)
ERROR_INK = QColor(0xDC, 0x58, 0x58)
# The Preview's paper. Named here with the rest of the fixed colours rather than
# written into the renderer, because it is a *theme* decision that happens not to
# vary: a glyph sheet's own palette is what the preview shows, and a paper that
# followed the window's surface would tint the art on one theme and not the
# other. Deliberately not black — an all-black glyph still has to be visible on
# it, and the grid lines above have to read without glowing.
PREVIEW_PAPER = QColor(0x18, 0x18, 0x1C)
# The rule the Preview draws over that paper, one line per pixel of the box, as
# the raw view rules string boundaries: faint enough that it does not compete
# with the art, and fixed with the paper rather than following the window.
PREVIEW_GRID = QColor(0xFF, 0xFF, 0xFF, 28)
# The amber wash behind a Files row that opened as something other than what it
# says — a file that is not there, or a read that had to give something up.
NOTICE_WASH = QColor(WARNING_INK.red(), WARNING_INK.green(), WARNING_INK.blue(), 40)


@dataclass(frozen=True)
class _PaletteSpec:
    """One theme's palette as data: a seed surface (``None`` for the style's
    standard palette) and the roles that override what derivation gets wrong."""

    surface: QColor | None = None
    roles: Mapping[QPalette.ColorRole, QColor] = field(default_factory=dict)
    disabled: Mapping[QPalette.ColorRole, QColor] = field(default_factory=dict)


_DARK_SURFACE = QColor(0x35, 0x35, 0x35)
_DARK_ROLES = {
    QPalette.ColorRole.Base: QColor(0x2B, 0x2B, 0x2B),
    QPalette.ColorRole.AlternateBase: QColor(0x30, 0x30, 0x30),
    QPalette.ColorRole.Highlight: QColor(0x2A, 0x6E, 0xB8),
    QPalette.ColorRole.HighlightedText: QColor(Qt.GlobalColor.white),
    QPalette.ColorRole.ToolTipBase: QColor(0x3C, 0x3C, 0x3C),
    QPalette.ColorRole.ToolTipText: QColor(0xE6, 0xE6, 0xE6),
    QPalette.ColorRole.PlaceholderText: QColor(0xFF, 0xFF, 0xFF, 0x66),
    QPalette.ColorRole.Link: QColor(0x54, 0xA6, 0xFF),
}
_DARK_DISABLED_TEXT = QColor(0x7A, 0x7A, 0x7A)
_DARK_DISABLED = {
    QPalette.ColorRole.WindowText: _DARK_DISABLED_TEXT,
    QPalette.ColorRole.Text: _DARK_DISABLED_TEXT,
    QPalette.ColorRole.ButtonText: _DARK_DISABLED_TEXT,
    QPalette.ColorRole.HighlightedText: _DARK_DISABLED_TEXT,
    QPalette.ColorRole.Highlight: QColor(0x45, 0x45, 0x45),
}

_PALETTES: dict[str, _PaletteSpec] = {
    "light": _PaletteSpec(),
    "dark": _PaletteSpec(
        surface=_DARK_SURFACE, roles=_DARK_ROLES, disabled=_DARK_DISABLED
    ),
}


def palette_for(name: str, style: QStyle) -> QPalette:
    """``name``'s palette as ``style`` would wear it."""
    spec = _PALETTES.get(name, _PALETTES["light"])
    palette = (
        style.standardPalette() if spec.surface is None else QPalette(spec.surface)
    )
    for role, color in spec.roles.items():
        palette.setColor(role, color)
    for role, color in spec.disabled.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, color)
    return palette


def apply_theme(app: QApplication | None, name: str) -> None:
    """Put the theme called ``name`` on the running application, live."""
    if app is None:
        app = QApplication.instance()
    if app is None:
        return
    app.styleHints().setColorScheme(
        Qt.ColorScheme.Dark if name == "dark" else Qt.ColorScheme.Light
    )
    style = QStyleFactory.create("Fusion")
    app.setPalette(palette_for(name, style))
    app.setStyle(style)
    app.setProperty("mapchar_theme", name)


__all__ = [
    "ERROR_INK",
    "NOTICE_WASH",
    "PREVIEW_GRID",
    "PREVIEW_PAPER",
    "THEMES",
    "WARNING_INK",
    "apply_theme",
    "palette_for",
]
