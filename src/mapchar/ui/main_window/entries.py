"""The open-entries list: adding, removing, ordering and renaming."""

from __future__ import annotations

import os

from PySide6.QtWidgets import QInputDialog

from mapchar.core.table import Table
from mapchar.project.tables import capture_overlay
from mapchar.project.workspace import (
    NAMED_UNIQUELY,
    Entry,
    EntryKind,
    Workspace,
    free_name,
    holder,
    normalize_path,
    within,
)
from mapchar.ui.entry_text import sorted_entries
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

    def _remove_entries(self, entries: list[Entry]) -> None:
        """Remove rows, once asked: a file takes its rows with it and a folder
        its contents, which the question names."""
        entries = [e for e in entries if e in self.workspace.entries]
        if not entries:
            return
        going = {id(e) for e in entries}
        children: list[Entry] = []
        for e in entries:
            for c in self.workspace.descendants(e):
                if id(c) not in going:
                    going.add(id(c))
                    children.append(c)
        msg = f"Remove {_names(entries)}?"
        if children:
            msg += f"\nAlso removes: {_names(children)}."
        if any(e.dirty for e in entries + children):
            msg += "\nUnsaved edits will be discarded."
        on_tables = self.workspace.blocks_on_tables(entries)
        orphaned = [b for b in on_tables if b not in children]
        if orphaned:
            msg += (
                f"\n{_names(orphaned)} read through it: their "
                "originals and notes are kept, but they cannot be read or edited "
                "until the table is loaded again."
            )
        if not self._ask("Remove Entries", msg):
            return
        for block in orphaned:
            block.stash_strings()
        with self._macro("Remove entries"), self.workspace.batch():
            for e in entries:
                if e in self.workspace.entries:
                    self._push_command(EntryCommand(self, e, add=False))

    def apply_entry_add(
        self, entry: Entry, index: int | None, children: list[Entry]
    ) -> None:
        with self.workspace.batch():
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
        order = self.workspace.reordered(self.workspace.entries, entry, before)
        self._push_order("Reorder entries", order)

    def _push_order(self, text: str, order: list[Entry]) -> None:
        """Push the list laid out in ``order``, each row in the folder it has."""
        self._push_layout(text, [(e, e.folder) for e in order])

    def _push_layout(self, text: str, layout: list[tuple[Entry, Entry | None]]) -> None:
        before = self.workspace.layout()
        if layout == before:
            return  # already there
        self._push_command(EntryOrderCommand(self, text, before, layout))

    def _place_entries(
        self, entries: list[Entry], container: Entry, before: Entry | None
    ) -> None:
        """Move rows under a file or folder, in front of ``before`` or last
        there — a drop, or New Folder gathering a selection — as one step.

        Only rows of the container's own file move, and never a folder into
        itself; a row inside a folder that moves goes with it. A folder is
        where a row is shown, so nothing else about the row changes.
        """
        if self._applying_undo or container.kind not in (
            EntryKind.FILE,
            EntryKind.FOLDER,
        ):
            return
        file_entry = container if container.kind is EntryKind.FILE else container.parent
        folder = container if container.kind is EntryKind.FOLDER else None
        candidates = [
            e
            for e in entries
            if e.is_child
            and e.parent is file_entry
            and e is not container
            and not within(container, e)
        ]
        ids = {id(e) for e in candidates}

        def carried(e: Entry) -> bool:
            above = e.folder
            while above is not None:
                if id(above) in ids:
                    return True
                above = above.folder
            return False

        moving = [e for e in candidates if not carried(e)]
        if not moving:
            return
        if before is not None and (
            holder(before) is not container or id(before) in ids
        ):
            rows = self.workspace.contents(container)
            at = rows.index(before) if before in rows else len(rows)
            before = next((r for r in rows[at:] if id(r) not in ids), None)
        layout = self.workspace.layout()
        try:
            for e in moving:
                e.folder = folder
            order = Workspace.moved(self.workspace.entries, moving, container, before)
            after = [(e, e.folder) for e in order]
        finally:
            for e, above in layout:
                e.folder = above
        self._push_layout(f"Move to {container.name}", after)

    def _new_folder(self, entry: Entry, rows: list[Entry]) -> Entry | None:
        """New Folder: on a file or a folder, an empty folder last inside it;
        on a row inside one, a folder in that row's place holding ``rows``.

        One undo step, and the new row opens for its name.
        """
        if not entry.is_child and entry.kind is not EntryKind.FILE:
            return None
        if entry.kind is EntryKind.FILE or (
            entry.kind is EntryKind.FOLDER and rows in ([], [entry])
        ):
            container, gathered = entry, []
        else:
            container, gathered = holder(entry), rows
        if container is None:
            return None
        file_entry = container if container.kind is EntryKind.FILE else container.parent
        taken = (
            e.name
            for e in self.workspace.children(file_entry)
            if e.kind is EntryKind.FOLDER
        )
        folder = Entry(
            EntryKind.FOLDER,
            free_name("New Folder", taken),
            file_entry.path,
            file_entry.extra_paths,
            parent=file_entry,
            folder=container if container.kind is EntryKind.FOLDER else None,
        )
        add = EntryCommand(self, folder, add=True)
        if gathered:
            # In the clicked row's place, which is inside the container.
            add.index = self.workspace.entries.index(entry)
        with self._macro("New folder"):
            self._push_command(add)
            if gathered:
                self._place_entries(gathered, folder, None)
        self.files_panel.begin_rename(folder)
        return folder

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
            order = self.workspace.reordered(order, entry, target)
        self._push_order(f"Move {len(entries)} entr(y/ies)", order)

    def _sort_entries(self, entry: Entry, key: str) -> None:
        """Put the group ``entry`` sits in into ``key`` order.

        The group comes from the panel for the reason :meth:`_reorder_entry`
        asks it there: on screen the rows are nested and sectioned, and it is
        that arrangement the user is asking to put in order.
        """
        if self._applying_undo:
            return
        wanted = sorted_entries(
            self.files_panel.siblings(entry), key, self.workspace.contents
        )
        # Laid out right to left: after each step the tail from that row on is
        # already in its final order, so the next move only has to reach its head.
        order = list(self.workspace.entries)
        for at in range(len(wanted) - 2, -1, -1):
            order = self.workspace.reordered(order, wanted[at], wanted[at + 1])
        self._push_order(f"Sort by {key.lower()}", order)

    def apply_entry_order(self, layout: list[tuple[Entry, Entry | None]]) -> None:
        """Lay the whole entry list out as ``layout`` — each row with its
        folder, in order: a reorder, a move between folders, and their undo."""
        self._applying_undo = True
        try:
            self.workspace.arrange(list(layout))
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
            name,
            (
                e.name
                for e in self.workspace.entries
                if e is not entry and e.kind is not EntryKind.FOLDER
            ),
        )

    def _show_entry(self, entry: Entry) -> None:
        """A Files row clicked: the entry on screen, and a block confined to its
        source — again when it already was, which is how a view drilled into
        one string comes back out.

        Not when the row brings back a block that was left reading its strings:
        it comes back on them, and the row that put it there is not a gesture
        away from them.
        """
        shown = entry is self._entry
        self._activate_entry(entry)
        if entry.kind is EntryKind.BLOCK and (shown or not self._in_string_view()):
            self._view_source(entry)

    def _on_entry_double(self, entry: Entry) -> None:
        if entry.kind is EntryKind.BOOKMARK:
            self._jump_to_bookmark(entry)
        elif entry.kind is EntryKind.TABLE:
            self._edit_table_entry(entry)

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


def _names(entries: list[Entry], most: int = 12) -> str:
    """The rows' names for a question, the tail counted rather than listed: a
    folder can hold hundreds of blocks."""
    names = [e.name for e in entries[:most]]
    if len(entries) > most:
        names.append(f"{len(entries) - most} more")
    return ", ".join(names)
