"""The Fonts dock and the glyph sheets it binds."""

from __future__ import annotations

from mapchar.core.capabilities import Capability, supports
from mapchar.core.font import Font, TextBox
from mapchar.project.workspace import Entry
from mapchar.ui.undo_commands import BoxCommand, FontCommand


class FontsMixin:
    """The Fonts dock and the glyph sheets it binds.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _edit_font_entry(self, entry: Entry) -> None:
        """Open the Preview window on this font, binding the current block to it."""
        if not supports(entry.kind, Capability.FONT_EDIT):
            return
        fonts = self.workspace.fonts()
        if entry not in fonts:
            return
        block = self._current_block()
        if block is not None:
            from dataclasses import replace

            box = block.box or TextBox()
            bound = replace(box, font_index=fonts.index(entry))
            if bound != block.box:
                # Binding is a box edit like any other: one undo step, and the
                # block reads unsaved until the project is written.
                self._push_command(BoxCommand(self, block, block.box, bound))
            self._sync_preview(force=True)
        else:
            self.preview_window.set_font(entry.font)
        self.preview_window.show()
        self.preview_window.tabs.setCurrentIndex(1)
        self.preview_window.raise_()

    def _bound_font(self, block: Entry | None) -> Entry | None:
        if block is None or block.box is None or block.box.font_index is None:
            return None
        fonts = self.workspace.fonts()
        i = block.box.font_index
        return fonts[i] if 0 <= i < len(fonts) else None

    def _on_font_changed(self, font: Font) -> None:
        font_entry = self._bound_font(self._entry)
        if font_entry is None or font_entry.font == font:
            return
        self._push_command(FontCommand(self, font_entry, font_entry.font, font))

    def apply_font(self, entry: Entry, font: Font | None, revision: int) -> None:
        entry.font = font
        self.workspace.stamp(entry, revision)
        self.fonts_panel.rebuild()
        if self._applying_undo:
            self.preview_window.set_font(font)
        else:
            # The Font tab's own fields already say this; only the drawing
            # and the panels have to catch up.
            self.preview_window.update_font(font)
        self._refresh_view()
