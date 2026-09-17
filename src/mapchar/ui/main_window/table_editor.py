"""The Table Editor window and the edits it makes."""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import replace

from PySide6.QtWidgets import QInputDialog

from mapchar.core.bits import Bits, bits_to_bytes
from mapchar.core.capabilities import Capability, supports
from mapchar.core.errors import MapcharError, TableError
from mapchar.core.notices import notice_lines
from mapchar.core.table import (
    ID_PATTERN,
    Table,
    TableSet,
    TokenKind,
    inherited,
    resolve,
)
from mapchar.core.table import (
    Entry as TableEntry,
)
from mapchar.core.tokens import render
from mapchar.engines.decode import DecodeRules, decode
from mapchar.project.formats.table_native import write_native
from mapchar.project.tables import (
    capture_overlay,
    fold_overlay,
    free_table_id,
    rebase_charset,
    set_charset,
    table_id_for,
)
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import TableCommand

SAMPLE_BYTES = 24
"""How far past an entry's key the sample line reads."""


class TableEditorMixin:
    """The Table Editor window and the edits it makes.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _add_selection_to_table(self) -> None:
        if not self._selection or self._doc is None:
            return
        s, e = self._selection
        table_entry = self.workspace.entry_for_table(self._current_table_id() or "")
        if table_entry is None:
            table = Table("main")
            table_entry = self._add_memory_table(table, "new.tbl")
            self._choose_table("main")
        self._edit_table_entry(table_entry)
        # One entry per byte: a selection is usually a run of characters.
        keys = [format(b, "08b") for b in self._doc.data[s:e]]
        self.table_editor.prefill(keys, s)

    def _table_sample(self, bits: str, at: int | None) -> str:
        """The Table Editor's sample line: what ``bits`` decode to in the
        file on screen, read on from ``at`` or from where they are first
        found, through the table being edited."""
        doc = self._doc
        entry = self.table_editor.entry
        if doc is None or entry is None or entry.table is None or len(bits) % 8:
            return ""
        key = bits_to_bytes(bits)
        if at is None:
            at = doc.data.find(key)
            if at < 0:
                return f"{key.hex(' ').upper()} is not in {self._entry.name}"
        tables = self.workspace.tables()
        try:
            ts = TableSet.build(entry.table, tables)
        except MapcharError as exc:
            return str(exc)
        end = min(len(doc.data), at + len(key) + SAMPLE_BYTES)
        result = decode(Bits(doc.data[at:end]), ts, 0, DecodeRules())
        text = render(result.tokens).replace("\n", "⏎")
        return f"at {self.address_spelling.format(at)}  {text}"

    def _table_inheritance(
        self, table: Table
    ) -> tuple[dict[str, tuple[TableEntry, str]], str]:
        """What ``table``'s includes give it, and what is wrong with it merged
        with them: the Table Editor's dimmed rows and its warning."""
        tables = self.workspace.tables()
        try:
            given = inherited(table, tables)
        except TableError as exc:
            return {}, exc.message
        try:
            resolve(table, tables)
        except TableError as exc:
            return given, exc.message
        return given, ""

    def _on_includes_chosen(self, entry: Entry, ids: tuple) -> None:
        """The Table Editor's Includes field: the table starts from other
        tables' entries, as one undo step."""
        if entry.table is None or tuple(ids) == entry.table.includes:
            return
        before = deepcopy(entry.table)
        entry.table.includes = tuple(ids)
        self._on_table_edited(entry, before)

    def _rename_table(self, entry: Entry | None) -> None:
        """Give ``entry``'s table another id, and every switch, include, block
        and reading that names it the new one, as one undo step."""
        if entry is None or entry.table is None:
            return
        old = entry.table.id
        new, ok = QInputDialog.getText(self, "Rename Table", "New id:", text=old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        if not ID_PATTERN.fullmatch(new):
            self._error(f"{new!r} is not a table id: letters, digits, _ . - only.")
            return
        if new in self.workspace.loaded_tables():
            self._error(f"A table called {new!r} is already loaded.")
            return
        with self._macro(f"Rename table {old} to {new}"):
            self._push_command(
                TableCommand(
                    self, entry, deepcopy(entry.table), _renamed(entry.table, new)
                )
            )
            for other in self.workspace.table_entries():
                table = other.table
                if table is None or other is entry:
                    continue
                if old not in table.switch_targets() and old not in table.includes:
                    continue
                self._push_command(
                    TableCommand(
                        self, other, deepcopy(table), _retargeted(table, old, new)
                    )
                )
            for block in self.workspace.of_kind(EntryKind.BLOCK):
                cfg = block.config
                if cfg is None or cfg.table_id != old:
                    continue
                self._push_block_edit(block, config=replace(cfg, table_id=new))
        # Readings are session state, not project state: they just follow.
        for e in self.workspace.entries:
            if e.session.table_id == old:
                e.session.table_id = new
        self._refresh_table_picks()
        self._refresh_view()

    def _show_table_editor(self) -> None:
        entry = self.workspace.entry_for_table(self._current_table_id() or "")
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

    def _on_table_edited(self, entry: Entry, before: Table) -> None:
        """One Table Editor change, as one undo step.

        The editor has already mutated the table and hands over what it held
        before, so the command is a plain before/after pair.
        """
        self._push_command(TableCommand(self, entry, before, deepcopy(entry.table)))

    def _on_charset_chosen(self, entry: Entry, charset: str) -> None:
        """The Table Editor's Charset pick: the table moves onto ``charset``
        with its edits, as one undo step."""
        if entry.table is None or entry.table.charset == charset:
            return
        before = deepcopy(entry.table)
        set_charset(entry, charset, self.registry)
        self._on_table_edited(entry, before)

    def apply_table(self, entry: Entry, snapshot: Table, revision: int) -> None:
        """Put ``snapshot`` back on the entry, its ``Table`` keeping its identity.

        Contents are restored rather than the object swapped out: the editor
        and the views hold the same ``Table`` object, and the command keeps
        holding the state it was handed, which must not be edited in place.
        """
        table = entry.table
        if table is None:
            entry.table = deepcopy(snapshot)
        else:
            # A step across a change of charset: the baseline follows, so the
            # overlay is measured against the file on the charset it now has.
            base = entry.file_table
            if base is not None and base.charset != snapshot.charset:
                rebase_charset(entry, snapshot.charset, self.registry)
            table.replace_with(snapshot)
        # Re-measured against the file rather than accumulated, so an undo and a
        # redo leave the project holding exactly what the table now says.
        capture_overlay(entry)
        self.workspace.stamp(entry, revision)
        shown = self.table_editor.entry
        # A table that includes the one changed shows what it gives it anew.
        if shown is entry or (
            shown is not None and shown.table is not None and shown.table.includes
        ):
            self.table_editor.set_entry(shown)
        self._tables_changed()

    def _save_table_entry(self, entry: Entry | None, ask: bool = False) -> None:
        if entry is None or entry.table is None:
            return
        path = entry.path
        if ask or not path or entry.dialect != "native":
            path = self._pick_save("Save Table As File", path or "", "Tables (*.tbl)")
            if not path:
                return
        if not self._write_text(path, write_native(entry.table)):
            return
        entry.path = path
        entry.dialect = "native"
        entry.name = os.path.basename(path)
        fold_overlay(entry)  # the file now says it; the project need not
        entry.table_id = None  # ...nor its id
        self.workspace.mark_saved(entry)
        self.files_panel.refresh_labels()
        self.tables_panel.rebuild()
        self.statusBar().showMessage(f"Saved {path}", 4000)

    def _new_table_dialog(self, *, start: bool = False) -> Entry | None:
        """Write an empty native table file where the user picks, and register it.

        The table is named after the file, numbered up past a loaded table of the
        same name. ``start`` makes it the start table; either way it opens in the
        Table Editor, which is where an empty table is filled.
        """
        path = self._pick_save("New Table", "new.tbl", "Tables (*.tbl)")
        if not path:
            return None
        if self.workspace.find_table(path) is not None:
            self._error(f"{os.path.basename(path)} is already open as a table.")
            return None
        table = Table(free_table_id(table_id_for(path), self.workspace.loaded_tables()))
        if not self._write_text(path, write_native(table)):
            return None
        entry = self.open_table(path, "native")
        if entry is None:
            return None
        if start:
            self._choose_table(table.id)
        self._edit_table_entry(entry)
        return entry


def _renamed(table: Table, new_id: str) -> Table:
    after = deepcopy(table)
    after.id = new_id
    return after


def _retargeted(table: Table, old: str, new: str) -> Table:
    """``table`` with every switch parameter and include naming ``old`` naming
    ``new``."""
    after = deepcopy(table)
    if old in after.includes:
        after.includes = tuple(new if i == old else i for i in after.includes)
    for entry in list(after.entries.values()):
        if entry.kind is not TokenKind.SWITCH:
            continue
        params = tuple(
            replace(p, table_id=new) if p.table_id == old else p for p in entry.params
        )
        if params != entry.params:
            after.add(replace(entry, params=params), replace=True)
    return after
