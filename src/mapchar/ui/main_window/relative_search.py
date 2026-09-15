"""Relative search hits and the tables built from them."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtWidgets import QInputDialog

from mapchar.core.table import Entry as TableEntry
from mapchar.core.table import Table
from mapchar.engines.relsearch import Hit, entries_from_base, entries_from_hit
from mapchar.ui.alphabets import RUN_NAMES
from mapchar.ui.undo_commands import TableCommand


class RelativeSearchMixin:
    """Relative search hits and the tables built from them.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    _RUN_NAMES = RUN_NAMES
    """What the Build table dialog calls each alphabet run: the Table Editor's
    Fill names, so one hit and one fill offer the same alphabets."""

    def _hit_entries(self, hit: Hit) -> list[TableEntry] | None:
        """The entries a hit seeds, for the alphabets the user picks."""
        found = [r for r in self._RUN_NAMES if r in hit.bases]
        if not found:
            return None
        names = [self._RUN_NAMES[r] for r in found]
        options = ([" ".join(names)] if len(found) > 1 else []) + names + ["Custom…"]
        choice, ok = QInputDialog.getItem(
            self, "Build Table", "Alphabets to lay out:", options, 0, False
        )
        if not ok:
            return None
        if choice == "Custom…":
            chars, ok = QInputDialog.getText(
                self,
                "Build Table",
                f"Characters in code order from {names[0]}'s base:",
            )
            if not ok or not chars:
                return None
            return entries_from_base(
                hit.bases[found[0]], hit.width * 8, hit.endian, chars
            )
        want = tuple(r for r in found if self._RUN_NAMES[r] in choice.split())
        return entries_from_hit(hit, want)

    def _build_table_from_hit(self, hit: Hit) -> None:
        entries = self._hit_entries(hit)
        if not entries:
            return
        table_id = self._current_table_id()
        target = self.workspace.entry_for_table(table_id or "")
        if target is not None and self._ask(
            "Build Table",
            f"Add {len(entries)} entries to @{table_id}? (No creates a new table)",
        ):
            # Through a command like every other table change, so the entries
            # added here go back out on one undo.
            before = target.table
            after = deepcopy(before)
            added = 0
            for entry in entries:
                if entry.bits not in after.entries:
                    after.add(entry)
                    added += 1
            if added:
                self._push_command(TableCommand(self, target, deepcopy(before), after))
            self.statusBar().showMessage(f"Added {added} entries to @{table_id}", 4000)
        else:
            name = f"relsearch_{hit.offset:X}"
            table = Table(name)
            for entry in entries:
                table.add(entry)
            self._add_memory_table(table, f"{name}.tbl")
            self._choose_table(name)
        self._tables_changed()
