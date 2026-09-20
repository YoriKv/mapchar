"""The Hex dock's dump, and what the field beside it finds."""

from __future__ import annotations


class HexViewMixin:
    """The Hex dock's dump, and what the field beside it finds.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _sync_hex_panel(self) -> None:
        """Hand the dock the bytes, the position and the selection. It spells its
        own address column, from the window's ``AddressSpelling``.

        Only while the dock is visible: the dump is the most expensive surface in
        the window to render and the least often open.
        """
        if not self.hex_dock.isVisible():
            return
        doc = self._doc
        if doc is None:
            self.hex_panel.set_data(b"", 0, None)
        else:
            self.hex_panel.set_data(doc.data, self._offset, self._selection)

    def _find_text_or_bytes(self, text: str, backwards: bool = False) -> None:
        """The Hex dock's find field: the same search as the Find bar's, which
        takes the text over so it stays the window's current search."""
        self.find_row.set_text(text)
        self._find_bytes(again=True, backwards=backwards)
