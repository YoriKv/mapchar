"""The raw view's context menu and what it copies."""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from mapchar.core.bits import Bits
from mapchar.engines.decode import DecodeRules, decode


class RawViewMixin:
    """The raw view's context menu and what it copies.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _raw_menu(self, pos: QPoint) -> None:
        if self._doc is None:
            return
        menu = QMenu(self)
        sel = self._selection
        menu.addAction(
            "New &Block from Selection",
            lambda: self._new_block(*sel) if sel else self._new_block(),
        )
        menu.addAction("New Boo&kmark", self._new_bookmark)
        if sel:
            ptr_rec = self._doc.string_for_pointer(sel[0])
            if ptr_rec is not None:
                menu.addAction(
                    "&Jump to Pointer Target",
                    lambda: self._select_bytes(ptr_rec.start, ptr_rec.length),
                )
            str_rec = self._string_at(sel[0])
            if str_rec is not None and str_rec.pointers:
                p = str_rec.pointers[0]
                menu.addAction(
                    "Jump to &Pointer", lambda: self._select_bytes(p.address, p.size)
                )
            menu.addAction("&Add to Table…", self._add_selection_to_table)
            menu.addAction("&Search for Selection", self._search_selection)
            menu.addAction(
                "Copy &Hex",
                lambda: QApplication.clipboard().setText(
                    " ".join(f"{b:02X}" for b in self._doc.data[sel[0] : sel[1]])
                ),
            )
            menu.addAction("Copy &Text", self._copy_selection_text)
        menu.exec(pos)

    def _string_at(self, offset: int):
        """The string of the current document holding ``offset``."""
        return self._doc.string_at(offset) if self._doc is not None else None

    def _copy_selection_text(self) -> None:
        if not self._selection or self._doc is None:
            return
        tables = self._table_set()
        if tables is None:
            return
        s, e = self._selection
        r = decode(
            Bits(self._doc.data[s:e]), tables, 0, DecodeRules(end_terminated=False)
        )
        from mapchar.core.tokens import render

        QApplication.clipboard().setText(render(r.tokens))
