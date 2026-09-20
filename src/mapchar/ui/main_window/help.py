"""The Help menu's dialogs, and the View menu's theme rows."""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from mapchar.ui.help_dialogs import (
    AboutDialog,
    LegendDialog,
    ShortcutGuide,
    shortcut_sections,
)
from mapchar.ui.icon_font import themed_icon


class HelpMixin:
    """The Help menu's dialogs, and the View menu's theme rows.

    Three Help rows and a View row rather than one surface, but each is a menu
    row with nothing behind it but what it opens or repaints, and the theme row
    ends in the icon baking :class:`~mapchar.ui.icon_font.ThemedIcons` asks
    every window for.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_shortcuts(self) -> None:
        """Help ▸ Shortcuts…: the list as the window is actually bound.

        Built from the menu bar plus the surfaces' declared keys
        (:mod:`mapchar.ui.help_dialogs`), so a shortcut that moves is right here
        without a second edit.
        """
        ShortcutGuide(shortcut_sections(self), self).exec()

    def _show_legend(self) -> None:
        """Help ▸ Legend…: what every colour and mark in the views means."""
        LegendDialog(self).exec()

    def _about(self) -> None:
        AboutDialog(self).exec()

    def _set_theme(self, name: str) -> None:
        from mapchar.ui.theme import apply_theme

        apply_theme(QApplication.instance(), name)
        self.settings.setValue("theme", name)
        (self.theme_dark if name == "dark" else self.theme_light).setChecked(True)
        self._bake_icons()

    def _bake_icons(self) -> None:
        """Stamp the navigation bar's step arrows in the theme's button-text
        color."""
        for button, glyph in self._step_icons:
            button.setIcon(themed_icon(self, glyph, QPalette.ColorRole.ButtonText))
