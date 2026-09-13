"""Relative search hits and the tables built from them."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMessageBox

from mapchar.core.table import Entry as TableEntry
from mapchar.core.table import Table
from mapchar.engines.relsearch import Hit, entries_from_base, entries_from_hit
from mapchar.ui.table_editor import ALPHABETS


class RelativeSearchMixin:
    """Relative search hits and the tables built from them.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    _RUN_NAMES = {run: name for name, run in ALPHABETS.items()}
    """What the Build table dialog calls each alphabet run: the Table Editor's
    Fill names, so one hit and one fill offer the same alphabets."""

    def _hit_entries(self, hit: Hit) -> list[TableEntry] | None:
        """The entries a hit seeds, for the alphabets the user picks."""
        found = [r for r in self._RUN_NAMES if r in hit.bases]
        if not found:
            return None
        names = [self._RUN_NAMES[r] for r in found]
        options = ([" ".join(names)] if len(found) > 1 else []) + names + ["custom…"]
        choice, ok = QInputDialog.getItem(
            self, "Build table", "Alphabets to lay out:", options, 0, False
        )
        if not ok:
            return None
        if choice == "custom…":
            chars, ok = QInputDialog.getText(
                self,
                "Build table",
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
        table_id = self.table_pick.currentData()
        target = self.workspace.entry_for_table(table_id or "")
        if (
            target is not None
            and QMessageBox.question(
                self,
                "Build table",
                f"Add {len(entries)} entries to @{table_id}? (No creates a new table)",
            )
            == QMessageBox.StandardButton.Yes
        ):
            table = next(t for t in target.tables if t.id == table_id)
            added = 0
            for entry in entries:
                if entry.bits not in table.entries:
                    table.add(entry)
                    added += 1
            self.workspace.stamp(target)
            self.statusBar().showMessage(f"Added {added} entries to @{table_id}", 4000)
        else:
            name = f"relsearch_{hit.offset:X}"
            table = Table(name)
            for entry in entries:
                table.add(entry)
            self._add_memory_table(table, f"{name}.tbl")
            self._choose_table(name)
        self._tables_changed()
