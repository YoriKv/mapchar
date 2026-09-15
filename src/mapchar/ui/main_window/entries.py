"""The open-entries list: adding, removing, ordering and renaming."""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

from mapchar.core.table import Table
from mapchar.project.tables import capture_overlay
from mapchar.project.workspace import (
    NAMED_UNIQUELY,
    Entry,
    EntryKind,
    free_name,
    normalize_path,
)
from mapchar.ui.files_panel import SORT_KEYS, sorted_entries
from mapchar.ui.help_dialogs import submenus
from mapchar.ui.undo_commands import EntryCommand, EntryOrderCommand, RenameEntryCommand


class EntriesMixin:
    """The open-entries list: adding, removing, ordering and renaming.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _add_memory_table(self, table: Table, name: str) -> Entry:
        entry = Entry(EntryKind.TABLE, name, None, dialect="native", table=table)
        # No file behind it, so every entry is overlay: the project carries the
        # whole table until a Save As File gives it one.
        capture_overlay(entry)
        self._push_add(entry)
        self.workspace.stamp(entry)
        self._refresh_table_picks()
        return entry

    def _push_add(self, entry: Entry) -> None:
        if entry.kind in NAMED_UNIQUELY:
            entry.name = self._free_name(entry.name)
        self._push_command(EntryCommand(self, entry, add=True))

    def _blocks_on_tables(self, entries: list[Entry]) -> list[Entry]:
        """The blocks whose start table is in one of the table entries ``entries``.

        What a table's removal leaves behind: the block keeps its strings and
        its configuration, but nothing can decode the bytes again until the
        table is back, so the removal has to say so before it happens.
        """
        going = {
            e.table.id
            for e in entries
            if e.kind is EntryKind.TABLE and e.table is not None
        }
        if not going:
            return []
        return [
            b
            for b in self.workspace.of_kind(EntryKind.BLOCK)
            if b.config is not None and b.config.table_id in going
        ]

    def _remove_entries(self, entries: list[Entry]) -> None:
        entries = [e for e in entries if e in self.workspace.entries]
        if not entries:
            return
        names = ", ".join(e.name for e in entries)
        children = [c for e in entries for c in self.workspace.children(e)]
        msg = f"Remove {names}?"
        if children:
            msg += f"\nAlso removes: {', '.join(c.name for c in children)}."
        if any(e.dirty for e in entries + children):
            msg += "\nUnsaved edits will be discarded."
        orphaned = [b for b in self._blocks_on_tables(entries) if b not in children]
        if orphaned:
            msg += (
                f"\n{', '.join(b.name for b in orphaned)} read through it: their "
                "translations are kept, but they cannot be re-read or written "
                "until the table is loaded again."
            )
        answer = QMessageBox.question(self, "Remove Entries", msg)
        if answer != QMessageBox.StandardButton.Yes:
            return
        for block in orphaned:
            self._stash_strings(block)
        self.undo_stack.beginMacro("Remove entries")
        for e in entries:
            if e in self.workspace.entries:
                self._push_command(EntryCommand(self, e, add=False))
        self.undo_stack.endMacro()

    def apply_entry_add(
        self, entry: Entry, index: int | None, children: list[Entry]
    ) -> None:
        self.workspace.add(entry, index)
        for child in children:
            self.workspace.add(child)
        self._refresh_table_picks()
        self._update_title()

    def apply_entry_remove(self, entry: Entry) -> list[Entry]:
        removed = self.workspace.close(entry)
        if self._entry in removed:
            self._entry = None
            self._doc = None
            self._activate_entry(self.workspace.current)
        self._refresh_table_picks()
        self._update_title()
        return removed

    def _reorder_entry(self, entry: Entry, before: Entry | None) -> None:
        """Move a row so it sits in front of ``before`` — a drop, or a one-row
        Alt+Up/Down.

        Where it came *from* is asked of the panel rather than of the workspace:
        the entry list is flat and the rows are grouped, so the neighbour a row
        has to go back in front of is its neighbour *on screen*.
        """
        if self._applying_undo:
            return
        order = self._reordered(self.workspace.entries, entry, before)
        self._push_order("Reorder entries", order)

    @staticmethod
    def _reordered(
        entries: list[Entry], entry: Entry, before: Entry | None
    ) -> list[Entry]:
        """``entries`` with ``entry`` and its children lifted out and put back in
        front of ``before`` (at the end when it is ``None``)."""
        group = [entry] + [e for e in entries if e.parent is entry]
        rest = [e for e in entries if e not in group]
        at = rest.index(before) if before in rest else len(rest)
        return rest[:at] + group + rest[at:]

    def _push_order(self, text: str, order: list[Entry]) -> None:
        before = list(self.workspace.entries)
        if order == before:
            return  # already there
        self._push_command(EntryOrderCommand(self, text, before, order))

    def _move_entries(self, entries: list[Entry], delta: int) -> None:
        """Step every row in ``entries`` one place — Alt+Up/Down, Move Up/Down.

        One gesture, so one undo step however many rows moved: each is walked in
        the direction of travel, so the rows they pass shuffle the other way.
        """
        if self._applying_undo:
            return
        order = list(self.workspace.entries)
        ordered = sorted(
            (e for e in entries if e in order), key=order.index, reverse=delta > 0
        )
        for entry in ordered:
            target = self.files_panel.move_target(entry, delta)
            if target is False:
                continue
            order = self._reordered(order, entry, target)
        self._push_order(f"Move {len(entries)} entr(y/ies)", order)

    def _sort_entries(self, entry: Entry, key: str) -> None:
        """Put the group ``entry`` sits in into ``key`` order.

        The group comes from the panel for the reason :meth:`_reorder_entry`
        asks it there: on screen the rows are nested and sectioned, and it is
        that arrangement the user is asking to put in order.
        """
        if self._applying_undo:
            return
        wanted = sorted_entries(self.files_panel.siblings(entry), key)
        # Laid out right to left: after each step the tail from that row on is
        # already in its final order, so the next move only has to reach its head.
        order = list(self.workspace.entries)
        for at in range(len(wanted) - 2, -1, -1):
            order = self._reordered(order, wanted[at], wanted[at + 1])
        self._push_order(f"Sort by {key.lower()}", order)

    def apply_entry_order(self, order: list[Entry]) -> None:
        """Lay the whole entry list out in ``order`` — a reorder and its undo."""
        self._applying_undo = True
        try:
            self.workspace.replace(list(order), self.workspace.current)
        finally:
            self._applying_undo = False

    def _same_named(self, entry: Entry) -> Entry | None:
        """A table or font row already on ``entry``'s path."""
        if not entry.path:
            return None
        return next(
            (
                e
                for e in self.workspace.of_kind(entry.kind)
                if e.path and normalize_path(e.path) == normalize_path(entry.path)
            ),
            None,
        )

    def _free_name(self, name: str, entry: Entry | None = None) -> str:
        """``name``, numbered up until no row but ``entry`` itself is called that."""
        return free_name(
            name, (e.name for e in self.workspace.entries if e is not entry)
        )

    def _show_entry(self, entry: Entry) -> None:
        """A Files row clicked: the entry on screen, and a block confined to its
        source — again when it already was, which is how a view drilled into
        one string comes back out."""
        self._activate_entry(entry)
        if entry.kind is EntryKind.BLOCK:
            self._view_source(entry)

    def _on_entry_double(self, entry: Entry) -> None:
        if entry.kind is EntryKind.BOOKMARK:
            self._jump_to_bookmark(entry)
        elif entry.kind is EntryKind.TABLE:
            self._edit_table_entry(entry)

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
                "New &Block…",
                lambda: (self._activate_entry(entry), self._new_block()),
            )
            menu.addAction(
                "New Block from &Selection…",
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
                "&Edit…", lambda: (self._activate_entry(entry), self._edit_block())
            )
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
                EntriesMixin._only_these_live(submenu, live)
            if not any(action is spared for spared in live):
                action.setEnabled(False)

    def _panel_selection(self) -> list[Entry]:
        """What an entry command acts on: the Files panel's rows, else the
        current entry — a menu click does not move the keyboard focus."""
        selected = self.files_panel.selected_entries()
        if selected:
            return selected
        return [self._entry] if self._entry is not None else []

    def _rename(self, entry: Entry) -> None:
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=entry.name)
        if ok and name.strip():
            self._commit_rename(entry, name.strip())

    def _commit_rename(self, entry: Entry, name: str) -> None:
        """A rename from either surface — the Rename… dialog or the Files
        panel's inline editor — as one undo step."""
        if entry.kind in NAMED_UNIQUELY:
            name = self._free_name(name, entry)
        if name == entry.name:
            return
        self._push_command(RenameEntryCommand(self, entry, entry.name, name))

    def _reveal(self, path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def apply_entry_name(self, entry: Entry, name: str) -> None:
        entry.name = name
        self.files_panel.refresh_labels()
        self._refresh_view()
        self._update_title()
