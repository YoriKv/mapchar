"""Light and dark themes as palettes on the Fusion style, never stylesheets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Colours that must read the same in both themes.
TINT_END = QColor(220, 80, 80, 90)
TINT_CODE = QColor(80, 140, 220, 90)
TINT_SWITCH = QColor(200, 140, 40, 90)
TINT_RAW = QColor(128, 128, 128, 70)
TINT_POINTER = QColor(90, 200, 120, 110)
TINT_SELECTION = QColor(60, 140, 240, 110)
TINT_STRING_RULE = QColor(120, 120, 120, 160)
WARNING_INK = QColor(200, 130, 0)
ERROR_INK = QColor(210, 60, 60)


def _dark_palette() -> QPalette:
    p = QPalette()
    base = QColor(37, 37, 38)
    window = QColor(45, 45, 48)
    text = QColor(220, 220, 220)
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(50, 50, 52))
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, window)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    p.setColor(QPalette.ColorRole.Highlight, QColor(60, 120, 200))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Link, QColor(100, 160, 240))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))
    disabled = QColor(120, 120, 120)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return p


def apply_theme(app: QApplication, name: str) -> None:
    app.setStyle("Fusion")
    if name == "dark":
        app.setPalette(_dark_palette())
    else:
        app.setPalette(app.style().standardPalette())
    app.setProperty("mapchar_theme", name)


def ink(widget, color: QColor) -> None:
    """Override only the widget's WindowText."""
    p = widget.palette()
    p.setColor(QPalette.ColorRole.WindowText, color)
    widget.setPalette(p)


__all__ = ["Qt", "apply_theme", "ink"]
