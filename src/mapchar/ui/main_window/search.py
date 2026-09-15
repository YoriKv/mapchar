"""Searching the current file for bytes or text."""

from __future__ import annotations

from mapchar.core.errors import MapcharError


class SearchMixin:
    """Searching the current file for bytes or text.

    The Find bar under the navigation row holds the current search: Ctrl+F
    puts the keyboard there, and Find Next and Find Previous (F3 and Shift+F3,
    Enter and Shift+Enter in the bar, its arrows) walk the matches of whatever
    it says. The Hex panel's field and Search for Selection hand the bar their
    text first, so there is one current search wherever it was typed.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_search(self) -> None:
        if self._doc is not None:
            self.search_window.set_data(self._doc.data)
        self.search_window.show()
        self.search_window.raise_()
        self.search_window.activateWindow()

    def _show_scan(self) -> None:
        if self._doc is not None:
            self.scan_window.set_source(self._doc.data, self._table_set())
        self.scan_window.show()
        self.scan_window.raise_()
        self.scan_window.activateWindow()

    def _find_bytes(self, again: bool = False, backwards: bool = False) -> None:
        # Ctrl+F means "narrow what I am looking at": in a view that is the byte
        # search, and in the Files panel it is the filter field. One key, decided
        # by where the keyboard is.
        if not again and self.files_panel.has_focus():
            self.files_dock.show()
            self.files_dock.raise_()
            self.files_panel.focus_filter()
            return
        if self._doc is None:
            return
        text = self.find_row.text()
        if not again or not text:
            # Ctrl+F, or a Find Next with nothing to look for: the bar is where
            # the search is typed.
            self.find_row.focus()
            return
        needle = self._needle_from(text)
        if needle is None:
            return
        pos = self._next_match(needle, backwards)
        if pos is None:
            self.statusBar().showMessage("Not found", 3000)
            return
        self._select_bytes(pos, len(needle))

    def _next_match(self, needle: bytes, backwards: bool) -> int | None:
        """The nearest match either side of where the last one was, wrapping.

        Both directions are asked from the **selection** — the last match, when
        there is one — and from the view position when there is not. Not from a
        remembered forward-only cursor: that would make Shift+F3 after three F3s
        land where the third came from rather than on the second match. And not
        from the view offset alone, because selecting a match scrolls a row
        *above* it, which would hand the next search the same match again.
        Wrapping is what makes a search from the middle of a file reach the
        matches behind it.
        """
        data = self._doc.data
        if not needle:
            return None
        at = self._selection[0] if self._selection else self._offset
        if backwards:
            pos = data.rfind(needle, 0, max(at, 0))
            if pos < 0:
                pos = data.rfind(needle)  # wrap to the last match in the file
        else:
            pos = data.find(needle, at + 1)
            if pos < 0:
                pos = data.find(needle)  # wrap to the first
        return pos if pos >= 0 else None

    def _needle_from(self, text: str) -> bytes | None:
        """Hex bytes, or ``"quoted text"`` run through the encode engine.

        The encoder is what makes multi-character entries, ``[codes]`` and
        table switches searchable: a per-character table lookup would only
        find text the start table spells one byte at a time.
        """
        if text.startswith('"') and text.endswith('"') and len(text) >= 2:
            tables = self._table_set()
            if tables is None:
                self._error("Pick a start table to search for text.")
                return None
            from mapchar.engines.encode import encode

            try:
                result = encode(text[1:-1], tables, end_terminated=False)
            except MapcharError as exc:
                self._error(str(exc))
                return None
            if len(result.bits) % 8:
                self._error("The text does not end on a byte boundary.")
                return None
            return result.data
        try:
            return bytes.fromhex(text.replace("$", "").replace(" ", ""))
        except ValueError:
            self._error("Not hex bytes.")
            return None

    def _search_selection(self) -> None:
        """Search for Selection: the selected bytes, spelled into the Find bar
        as hex so the search shows and can be walked from there."""
        if not self._selection or self._doc is None:
            return
        s, e = self._selection
        self.find_row.set_text(self._doc.data[s:e].hex(" ").upper())
        self._find_bytes(again=True)
