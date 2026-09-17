"""Cut, copy, paste and duplicate whole entries."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from mapchar.project.projectfile import entries_from_payload, entries_payload
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import EntryCommand


class EntryClipboardMixin:
    """Cut, copy, paste and duplicate whole entries.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _clipboard_text(self) -> str:
        return QApplication.clipboard().text()

    def _clipboard_entries_available(self) -> bool:
        return bool(entries_from_payload(self._clipboard_text()))

    def _entry_group(self, entries: list[Entry]) -> list[Entry]:
        """``entries`` and the rows that travel with them, in list order.

        A file takes its blocks and bookmarks the way a removal does: they are
        windows into it and mean nothing without it, so a copy that left them
        behind would paste a ROM and lose the work of finding things inside it.
        A folder takes its contents the same way.
        """
        wanted = {id(e) for e in entries}
        for e in entries:
            wanted.update(id(c) for c in self.workspace.descendants(e))
        return [e for e in self.workspace.entries if id(e) in wanted]

    @staticmethod
    def _roots(entries: list[Entry]) -> list[Entry]:
        """``entries`` less the ones another of them holds, which go with it."""
        ids = {id(e) for e in entries}

        def held(e: Entry) -> bool:
            if id(e.parent) in ids:
                return True
            folder = e.folder
            while folder is not None:
                if id(folder) in ids:
                    return True
                folder = folder.folder
            return False

        return [e for e in entries if not held(e)]

    def _copy_entries(self, entries: list[Entry]) -> None:
        """Put entries on the clipboard: references plus settings, never bytes."""
        group = self._entry_group(entries)
        if not group:
            return
        QApplication.clipboard().setText(entries_payload(group))
        self.statusBar().showMessage(f"Copied {len(group)} entr(y/ies)", 4000)

    def _cut_entries(self, entries: list[Entry]) -> None:
        """Copy the rows and take them out.

        No confirmation, unlike Remove: a cut says where the rows are going,
        they are on the clipboard the moment they leave, and one Ctrl+Z brings
        them back. Remove says only "gone", which is why it asks.
        """
        group = self._entry_group(entries)
        if not group:
            return
        self._copy_entries(entries)
        roots = self._roots(entries)
        with self._macro("Cut entries"), self.workspace.batch():
            for e in roots:
                if e in self.workspace.entries:
                    self._push_command(EntryCommand(self, e, add=False))

    def _duplicate_entries(self, entries: list[Entry]) -> None:
        """A second row over the same region, without touching the clipboard.

        Only where a second row can mean anything — a block, a bookmark, or a
        folder with its contents — and in the folder the original sits in. A
        file's identity is its path, and Ctrl+D doing nothing at all would read
        as a bug, so it answers with a message instead of a dead key.
        """
        movable = self._roots([e for e in entries if e.is_child])
        if not movable:
            self.statusBar().showMessage(
                "A ROM, table or font can only be open once — duplicate one of "
                "its blocks or bookmarks instead.",
                5000,
            )
            return
        # Round-tripped through the payload rather than copied by hand, so a
        # duplicate is the same operation as a paste and cannot drift from it.
        group = self._entry_group(movable)
        copies = entries_from_payload(entries_payload(group))
        for original, copy in zip(group, copies, strict=True):
            if copy.folder is None and original in movable:
                copy.folder = original.folder
        self._place_copies(copies, None, "Duplicated")

    def _paste_entries(self, target: Entry | None) -> None:
        copied = entries_from_payload(self._clipboard_text())
        if not copied:
            self.statusBar().showMessage("Nothing on the clipboard to paste here", 4000)
            return
        self._place_copies(copied, target, "Pasted")

    def _place_copies(
        self, copied: list[Entry], target: Entry | None, verb: str
    ) -> None:
        """Add ``copied`` as one undo step, aimed at ``target``'s file.

        A file already open is not added twice — a row's identity is its path —
        and its children stay behind with it, so pasting a whole ROM back into
        the project it came from selects that ROM rather than doubling its
        blocks. A child pasted on its own attaches to the file the paste aimed
        at, which is how one finds the same regions in a second dump — inside
        the folder aimed at, or the one the row aimed at sits in.
        """
        host = target if target is None or not target.is_child else target.parent
        into = None
        if target is not None and target.kind is EntryKind.FOLDER:
            into = target
        elif target is not None and target.is_child:
            into = target.folder
        placed: list[Entry] = []
        already: list[Entry] = []
        left_behind: set[int] = set()
        for entry in copied:
            if not entry.is_child:
                existing = (
                    self.workspace.find_file(entry.path)
                    if entry.kind is EntryKind.FILE and entry.path
                    else self._same_named(entry)
                )
                if existing is not None:
                    already.append(existing)
                    left_behind.add(id(entry))
                    continue
                placed.append(entry)
                continue
            if entry.parent is not None and id(entry.parent) in left_behind:
                continue
            if entry.parent is None or entry.parent not in copied:
                parent = host or (
                    self.workspace.find_file(entry.path) if entry.path else None
                )
                if parent is None:
                    already.append(entry)
                    continue
                entry.parent = parent
                entry.path = parent.path
                entry.extra_paths = parent.extra_paths
                if entry.folder is None or entry.folder not in copied:
                    # A folder keeps what it was copied with; a loose row lands
                    # where the paste aimed, when that is a folder of this file.
                    held = entry.folder is not None and entry.folder.parent is parent
                    if not held:
                        entry.folder = (
                            into if into is not None and into.parent is parent else None
                        )
            elif id(entry.parent) in left_behind:
                continue
            placed.append(entry)
        # A child whose own parent was left behind re-aims at the row already
        # holding that path, so nothing arrives loose beside the files.
        for entry in placed:
            parent = entry.parent
            if entry.is_child and parent is not None and parent not in placed:
                existing = (
                    self.workspace.find_file(parent.path) if parent.path else None
                )
                if existing is not None:
                    entry.parent = existing
        for entry in placed:
            if entry.folder is not None and entry.folder not in placed:
                if entry.folder not in self.workspace.entries:
                    entry.folder = None
            if entry.kind is not EntryKind.FOLDER:
                entry.name = self._free_name(entry.name)
        if not placed:
            if already:
                self._activate_entry(already[0])
                self.statusBar().showMessage(
                    f"Already open: {', '.join(e.name for e in already)}", 4000
                )
            else:
                self.statusBar().showMessage("Nothing to paste here", 4000)
            return
        with self._macro(f"{verb} entries"), self.workspace.batch():
            for entry in placed:
                self._push_command(EntryCommand(self, entry, add=True))
        first = next((e for e in placed if e.kind is not EntryKind.BOOKMARK), None)
        if first is not None:
            self._activate_entry(first)
        note = f" ({len(already)} already open)" if already else ""
        self.statusBar().showMessage(f"{verb} {len(placed)} entr(y/ies){note}", 4000)

    def _cut_selection(self) -> None:
        self._cut_entries(self._panel_selection())

    def _copy_selection(self) -> None:
        self._copy_entries(self._panel_selection())

    def _paste_selection(self) -> None:
        selected = self._panel_selection()
        self._paste_entries(selected[0] if selected else None)

    def _duplicate_selection(self) -> None:
        self._duplicate_entries(self._panel_selection())
