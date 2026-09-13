"""The Table Editor window and the edits it makes."""

from __future__ import annotations

import os

from mapchar.core.capabilities import Capability, supports
from mapchar.core.notices import notice_lines
from mapchar.core.table import Table
from mapchar.project.formats.table_native import write_native
from mapchar.project.tables import capture_overlay, fold_overlay
from mapchar.project.workspace import Entry
from mapchar.ui.undo_commands import TableCommand


class TableEditorMixin:
    """The Table Editor window and the edits it makes.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _add_selection_to_table(self) -> None:
        if not self._selection or self._doc is None:
            return
        s, e = self._selection
        table_entry = self.workspace.entry_for_table(
            self.table_pick.currentData() or ""
        )
        if table_entry is None:
            table = Table("main")
            table_entry = self._add_memory_table(table, "new.tbl")
            self._choose_table("main")
        self._edit_table_entry(table_entry)
        self.table_editor.select_table(self.table_pick.currentData())
        from mapchar.core.bits import bytes_to_bits

        self.table_editor.prefill(bytes_to_bits(self._doc.data[s : min(e, s + 4)]))

    def _show_table_editor(self) -> None:
        entry = self.workspace.entry_for_table(self.table_pick.currentData() or "")
        if entry is None:
            tables = self.workspace.table_entries()
            entry = tables[0] if tables else None
        self._edit_table_entry(entry)

    def _edit_table_entry(self, entry: Entry | None) -> None:
        # The Table Editor is where a table entry is edited, and the only thing
        # it can be handed: activating any other kind reaches this through the
        # Files panel too.
        if entry is not None and not supports(entry.kind, Capability.TABLE_EDIT):
            return
        self.table_editor.set_entry(entry)
        if entry is not None and self.table_pick.currentData():
            self.table_editor.select_table(self.table_pick.currentData())
        notices = entry.notices if entry else ()
        if notices:
            # The status line has room for one line, so the messages go there and
            # every detail follows in the tooltip: a conversion notice that says
            # what it could not keep is the one worth reading in full.
            self.table_editor.status.setText("; ".join(n.message for n in notices[:5]))
            self.table_editor.status.setToolTip(
                "\n".join(line for n in notices for line in notice_lines(n))
            )
        else:
            self.table_editor.status.setToolTip("")
        self.table_editor.show()
        self.table_editor.raise_()

    def _on_table_edited(self, entry: Entry, before: list) -> None:
        """One Table Editor change, as one undo step.

        The editor has already mutated the tables and hands over what they
        held before, so the command is a plain before/after pair.
        """
        from copy import deepcopy

        self._push_command(TableCommand(self, entry, before, deepcopy(entry.tables)))

    def apply_tables(self, entry: Entry, tables: list, revision: int) -> None:
        """Put ``tables`` back on the entry, each ``Table`` keeping its identity.

        Contents are restored rather than the objects swapped out: the editor
        and the views hold the same ``Table`` objects, and the command keeps
        holding the state it was handed, which must not be edited in place.
        """
        from copy import deepcopy

        live = {t.id: t for t in entry.tables}
        restored = []
        for snapshot in tables:
            table = live.get(snapshot.id)
            if table is None:
                restored.append(deepcopy(snapshot))
                continue
            table.charset = snapshot.charset
            for bits in list(table.entries):
                table.remove(bits)
            for table_entry in snapshot.entries.values():
                table.add(table_entry)
            restored.append(table)
        entry.tables = restored
        # Re-measured against the file rather than accumulated, so an undo and a
        # redo leave the project holding exactly what the tables now say.
        capture_overlay(entry)
        self.workspace.stamp(entry, revision)
        if self.table_editor._entry is entry:
            self.table_editor.set_entry(entry)
        self._tables_changed()

    def _save_table_entry(self, entry: Entry | None, ask: bool = False) -> None:
        if entry is None:
            return
        path = entry.path
        if ask or not path or entry.dialect != "native":
            path = self._pick_save("Save Table as Native", path or "", "Tables (*.tbl)")
            if not path:
                return
        if not self._write_text(path, write_native(entry.tables)):
            return
        entry.path = path
        entry.dialect = "native"
        entry.name = os.path.basename(path)
        fold_overlay(entry)  # the file now says it; the project need not
        self.workspace.mark_saved(entry)
        self.files_panel.refresh_labels()
        self.tables_panel.rebuild()
        self.statusBar().showMessage(f"Saved {path}", 4000)
