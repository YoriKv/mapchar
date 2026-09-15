"""The Files panel's context menu: what each kind of row can be asked."""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.files_panel import SORT_KEYS
from mapchar.ui.help_dialogs import submenus


class FilesMenuMixin:
    """The Files panel's context menu: what each kind of row can be asked.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _files_menu(self, entry: Entry | None, pos: QPoint) -> None:
        self._build_files_menu(entry).exec(pos)

    def _build_files_menu(self, entry: Entry | None) -> QMenu:
        """The Files panel's context menu, by kind.

        A right-click inside a multi-row selection keeps it, and the menu is
        then about the set: Remove and the two moves act on all of it and every
        other row goes dead — each of them *is* something the clicked row could
        do, just not while it is one of several, so it is greyed rather than
        dropped.
        """
        menu = QMenu(self)
        if entry is None:
            menu.addAction("Open RO&M…", self._open_rom_dialog)
            menu.addAction("Open &Table…", self._open_table_dialog)
            menu.addAction("New Ta&ble…", lambda: self._new_table_dialog())
            paste = menu.addAction("&Paste", lambda: self._paste_entries(None))
            paste.setEnabled(self._clipboard_entries_available())
            return menu
        selected = self.files_panel.selected_entries()
        acting = selected if len(selected) > 1 and entry in selected else [entry]
        if entry.kind is EntryKind.FILE:
            menu.addAction(
                "New &Block",
                lambda: (self._activate_entry(entry), self._new_block()),
            )
            menu.addAction(
                "New Block from &Selection",
                lambda: (self._activate_entry(entry), self._new_block(*self._sel())),
            ).setEnabled(entry is self._current_file() and self._selection is not None)
            menu.addAction(
                "New Boo&kmark",
                lambda: (self._activate_entry(entry), self._new_bookmark()),
            )
            menu.addSeparator()
            menu.addAction("Edit File Cont&ainer…", lambda: self._edit_container(entry))
            menu.addAction("Container In&fo…", lambda: self._container_info(entry))
            menu.addAction(
                "&Dump All Blocks…",
                lambda: (self._activate_entry(entry), self._dump(all_blocks=True)),
            )
        if entry.kind is EntryKind.BLOCK:
            menu.addAction(
                "&Dump…", lambda: (self._activate_entry(entry), self._dump())
            )
            menu.addAction("&Jump to Source", lambda: self._jump_to_source(entry))
        if entry.kind is EntryKind.BOOKMARK:
            menu.addAction("&Jump to Bookmark", lambda: self._jump_to_bookmark(entry))
        if entry.kind is EntryKind.TABLE:
            menu.addAction("&Edit…", lambda: self._edit_table_entry(entry))
            menu.addAction(
                "Save &As File…", lambda: self._save_table_entry(entry, ask=True)
            )
            menu.addAction("New Ta&ble…", lambda: self._new_table_dialog())
        menu.addSeparator()
        menu.addAction("&Write", lambda: self._write_entry(entry))
        if entry.kind is EntryKind.BLOCK:
            export = menu.addMenu("E&xport")
            for label, kind in (("&TSV…", "tsv"), ("C&SV…", "csv"), ("&PO…", "po")):
                export.addAction(
                    label,
                    lambda kind=kind: (self._activate_entry(entry), self._export(kind)),
                )
            export.addSeparator()
            export.addAction(
                "&Cartographer Command File…",
                lambda: (self._activate_entry(entry), self._export_cartographer()),
            )
            export.addAction(
                "&Atlas Script…",
                lambda: (self._activate_entry(entry), self._export_atlas()),
            )
        menu.addSeparator()
        menu.addAction("&Rename…", lambda: self._rename(entry))
        menu.addAction("Cu&t", lambda: self._cut_entries(acting))
        menu.addAction("&Copy", lambda: self._copy_entries(acting))
        paste = menu.addAction("&Paste", lambda: self._paste_entries(entry))
        paste.setEnabled(self._clipboard_entries_available())
        menu.addAction("Dupl&icate", lambda: self._duplicate_entries(acting))
        menu.addSeparator()
        up = menu.addAction("Move &Up", lambda: self._move_entries(acting, -1))
        down = menu.addAction("Move Dow&n", lambda: self._move_entries(acting, 1))
        sort = menu.addMenu("S&ort By")
        for key in SORT_KEYS:
            sort.addAction(f"&{key}", lambda key=key: self._sort_entries(entry, key))
        sort.actions()[SORT_KEYS.index("Offset")].setEnabled(entry.is_child)
        if entry.path:
            menu.addAction("Show in File &Manager", lambda: self._reveal(entry.path))
        menu.addSeparator()
        remove = menu.addAction("Remo&ve", lambda: self._remove_entries(acting))
        if len(acting) > 1:
            self._only_these_live(menu, [remove, up, down])
        return menu

    def _sel(self) -> tuple[int, int]:
        """The raw view's selection as a ``(start, stop)`` pair for a new block."""
        return self._selection if self._selection else (self._offset, self._offset)

    @staticmethod
    def _only_these_live(menu: QMenu, live: list[QAction]) -> None:
        """Grey every row of ``menu`` and its submenus except ``live``.

        Applied over the finished menu rather than threaded through each branch
        above: the question is not any one row's, it is "does this name a single
        entry?", and the answer is yes for all but the three handed in.
        """
        below = submenus(menu)
        for action in menu.actions():
            submenu = below.get(action)
            if submenu is not None:
                FilesMenuMixin._only_these_live(submenu, live)
            if not any(action is spared for spared in live):
                action.setEnabled(False)
