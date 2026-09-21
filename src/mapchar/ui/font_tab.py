"""The Preview window's Font tab: the system font the preview draws through.

The font is the app's, not a block's — one family and size for every preview,
stored beside the theme (:mod:`mapchar.ui.preview_font`). The tab picks it and
shows a line of it; everything drawn through it redraws on
:attr:`FontTab.font_changed`.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFontComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mapchar.ui.number_fields import number_spin
from mapchar.ui.preview_font import MAX_SIZE, MIN_SIZE, preview_font
from mapchar.ui.widgets import ElidedLabel

SAMPLE = "The quick brown fox — 0123456789 — あいうカタカナ"
"""What the tab draws in the chosen font: Latin, digits and kana, since a
family that cannot draw a game's kana is the thing worth seeing early."""


class FontTab(QWidget):
    """Which system font every preview draws through."""

    font_changed = Signal()
    """The app's preview font changed: redraw whatever was drawn through it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._syncing = False
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.family = QFontComboBox()
        self.family.setToolTip("Font family for every preview")
        self.size = number_spin(MIN_SIZE, MAX_SIZE, 2)
        self.size.setToolTip("Point size for drawing and measuring")
        form.addRow("Family", self.family)
        form.addRow("Size", self.size)
        box.addLayout(form)
        self.sample = QLabel(SAMPLE)
        self.sample.setWordWrap(True)
        box.addWidget(self.sample)
        note = ElidedLabel(
            "One font for the whole app, not for this block: it stands in for "
            "the game's own."
        )
        box.addWidget(note)
        box.addStretch(1)
        self.reload()
        self.family.currentFontChanged.connect(lambda _: self._emit())
        self.size.valueChanged.connect(lambda _: self._emit())

    def reload(self) -> None:
        """Show what the app's preview font is now."""
        chosen = preview_font()
        self._syncing = True
        try:
            self.family.setCurrentFont(QFont(chosen.family))
            self.size.setValue(chosen.size)
        finally:
            self._syncing = False
        self._show_sample()

    def _emit(self) -> None:
        if self._syncing:
            return
        preview_font().set_font(self.family.currentFont().family(), self.size.value())
        self._show_sample()
        self.font_changed.emit()

    def _show_sample(self) -> None:
        self.sample.setFont(preview_font().qfont)


__all__ = ["FontTab"]
