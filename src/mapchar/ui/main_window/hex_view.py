"""The Hex dock's dump, and what the field beside it finds."""

from __future__ import annotations


class HexViewMixin:
    """The Hex dock's dump.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _sync_hex_panel(self) -> None:
        """Hand the dock the bytes, the position, the selection and the address
        format — the last so its address column agrees with the navigation bar's
        rather than always spelling a flat offset.

        Only while the dock is visible: the dump is the most expensive surface in
        the window to render and the least often open.
        """
        if not self.hex_dock.isVisible():
            return
        doc = self._doc
        if doc is None:
            self.hex_panel.set_data(b"", 0, None, self._format_address)
        else:
            self.hex_panel.set_data(
                doc.data, self._offset, self._selection, self._format_address
            )

    def _find_text_or_bytes(self, text: str, backwards: bool = False) -> None:
        """The Hex dock's find field: hex bytes, or quoted text through the start
        table, searched in either direction from the view position."""
        if not text.strip() or self._doc is None:
            return
        needle = self._needle_from(text.strip())
        if needle is None:
            return
        self._find_needle = needle
        self._find_bytes(again=True, backwards=backwards)
