"""The prior-art exchange formats: Cartographer command files and Atlas scripts.

Both address the *file* rather than a block's own offsets, which is what they
have in common and what :meth:`~LegacyExchangeMixin._container_header` is for.
mapChar's own exchange — the script, the translator tables and PO files — is
:mod:`mapchar.ui.main_window.import_export`.
"""

from __future__ import annotations

import os

from mapchar.core.context import KEY_HEADER_SIZE
from mapchar.core.errors import MapcharError
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.exchange.addresses import shift_config
from mapchar.project.exchange.atlas import read_atlas_script, write_atlas
from mapchar.project.exchange.cartographer import (
    parse_command_file,
    write_command_file,
)


class LegacyExchangeMixin:
    """Cartographer command files and Atlas scripts, in and out.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _container_header(self, file_entry: Entry | None) -> int:
        """``file_entry``'s container header: what a file address carries and a
        block's offsets do not.

        Cartographer and Atlas both address the file, so an export adds this to
        every address it writes and an import subtracts it back off.
        """
        doc = self._load_document(file_entry) if file_entry is not None else None
        return int(doc.ctx.get(KEY_HEADER_SIZE, 0) or 0) if doc is not None else 0

    def _import_cartographer_dialog(self) -> None:
        path = self._pick_open("Import Cartographer Command File", "*.txt;;*")
        if path:
            self.import_cartographer(path)

    def import_cartographer(self, path: str) -> list[Entry]:
        """Blocks from a command file, with its tables, under the current ROM."""
        file_entry = self._current_file()
        if file_entry is None:
            self._error("Open the ROM the command file describes first.")
            return []
        text, notices = self._read_text(path)
        if text is None:
            return []
        try:
            cf = parse_command_file(text)
        except MapcharError as exc:
            self._error(f"Cannot import {path}: {exc}")
            return []
        base = os.path.dirname(os.path.abspath(path))
        notices += [n.message for n in cf.notices]
        created: list[Entry] = []
        # Cartographer addresses are file offsets; blocks address the payload
        # the container yields, which drops the file's header.
        header = self._container_header(file_entry)
        with self._macro(f"Import {os.path.basename(path)}"):
            for sub in cf.sub_tables:
                self.open_table(os.path.normpath(os.path.join(base, sub)), "abcde")
            for block in cf.blocks:
                table_path = os.path.normpath(os.path.join(base, block.table_file))
                table_entry = self.open_table(table_path, "abcde")
                table_id = block.table_id
                if table_id is None and table_entry is not None and table_entry.table:
                    table_id = table_entry.table.id
                if table_id is None:
                    notices.append(f"{block.name}: no table; block skipped")
                    continue
                from dataclasses import replace

                entry = Entry(
                    EntryKind.BLOCK,
                    block.name,
                    file_entry.path,
                    parent=file_entry,
                    config=shift_config(
                        replace(block.config, table_id=table_id), -header
                    ),
                )
                self._push_add(entry)
                created.append(entry)
        self._remember_dir(path)
        if created:
            self._activate_entry(created[0])
        self._report(
            "Cartographer Import",
            f"Imported {len(created)} block(s) from {os.path.basename(path)}",
            notices,
        )
        return created

    def _import_atlas_dialog(self) -> None:
        path = self._pick_open("Import Atlas Script", "*.txt;;*")
        if path:
            self.import_atlas(path)

    def import_atlas(self, path: str) -> int:
        """Translations from an Atlas script into the current block's strings."""
        entry = self._current_block(
            need_doc=True, complain="Select the block the script belongs to first."
        )
        if entry is None:
            return 0
        text, notices = self._read_text(path)
        if text is None:
            return 0
        script = read_atlas_script(text)
        notices += script.notices
        # The script addresses the file; the block's records address the payload.
        header = self._container_header(entry.parent)
        by_pointer = {p.address: r for r in entry.doc.strings for p in r.pointers}
        by_start = {r.start: r for r in entry.doc.strings}
        texts: dict[int, str] = {}
        for i, item in enumerate(script.strings):
            rec = None
            for addr in item.pointers:
                rec = by_pointer.get(addr - header)
                if rec is not None:
                    break
            if rec is None and item.insert_at is not None:
                rec = by_start.get(item.insert_at - header)
            if rec is None:
                notices.append(f"string {i}: no block string matches its address")
                continue
            texts[rec.index] = item.text
        applied = self._land_texts(
            {entry.name: texts}, f"Import {os.path.basename(path)}", notices
        )
        self._remember_dir(path)
        self._refresh_view()
        self._report(
            "Atlas Import",
            f"Imported {applied} string(s) from {os.path.basename(path)}",
            notices,
        )
        return applied

    def _export_atlas(self) -> None:
        entry = self._current_block(
            need_doc=True,
            need_tables=True,
            complain="Select a block with a start table to export.",
        )
        if entry is None:
            return
        tables = self._table_set()
        path = self._pick_save("Export Atlas Script", f"{entry.name}.txt", "*.txt")
        if not path:
            return
        table_files = {
            e.name if e.name.endswith(".tbl") else e.name + ".tbl": e.table
            for e in self.workspace.table_entries()
            if e.table is not None and e.table.id in tables.tables
        }
        header = self._container_header(entry.parent)
        export = write_atlas(
            entry.name,
            shift_config(entry.config, header),
            entry.doc.strings,
            tables,
            table_files,
            header=header,
        )
        folder = os.path.dirname(path)
        if not self._write_text(path, export.script):
            return
        for name, text in export.tables.items():
            if not self._write_text(os.path.join(folder, name), text):
                return
        self._remember_dir(path)
        self._report(
            "Atlas Export",
            f"Exported {path} and {len(export.tables)} table file(s)",
            export.notices,
        )

    def _export_cartographer(self) -> None:
        entry = self._current_block(
            need_config=True, complain="Select a block to export."
        )
        if entry is None:
            return
        path = self._pick_save(
            "Export Cartographer Command File", f"{entry.name}.txt", "*.txt"
        )
        if not path:
            return
        table_entry = self.workspace.entry_for_table(entry.config.table_id)
        table_file = (
            os.path.basename(table_entry.path)
            if table_entry and table_entry.path
            else "main.tbl"
        )
        header = self._container_header(entry.parent)
        text, notes = write_command_file(
            entry.name,
            shift_config(entry.config, header),
            table_file,
            table_id=entry.config.table_id or None,
        )
        if not text:
            self._error("\n".join(notes))
            return
        if not self._write_text(path, text):
            return
        self._remember_dir(path)
        self._report("Cartographer Export", f"Exported {path}", notes)
