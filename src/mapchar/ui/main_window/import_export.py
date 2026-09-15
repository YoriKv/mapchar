"""The exchange formats: command files, scripts and translator files."""

from __future__ import annotations

import os

from mapchar.core.context import KEY_HEADER_SIZE
from mapchar.core.errors import MapcharError
from mapchar.pipeline.exchange.atlas import read_atlas, write_atlas
from mapchar.pipeline.exchange.cartographer import (
    parse_command_file,
    shift_config,
    write_command_file,
)
from mapchar.pipeline.exchange.script_import import apply_script
from mapchar.project.formats.script import parse_script
from mapchar.project.formats.translator import (
    apply_records,
    read_delimited,
    read_po,
    records_for,
    write_delimited,
    write_po,
)
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import StringFieldCommand


class ImportExportMixin:
    """The exchange formats: command files, scripts and translator files.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

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
        text = self._read_text(path)
        if text is None:
            return []
        try:
            cf = parse_command_file(text)
        except MapcharError as exc:
            self._error(f"Cannot import {path}: {exc}")
            return []
        base = os.path.dirname(os.path.abspath(path))
        notices = [n.message for n in cf.notices]
        created: list[Entry] = []
        # Cartographer addresses are file offsets; blocks address the payload
        # the container yields, which drops the file's header.
        doc = self._load_document(file_entry)
        header = int(doc.ctx.get(KEY_HEADER_SIZE, 0) or 0) if doc is not None else 0
        self.undo_stack.beginMacro(f"Import {os.path.basename(path)}")
        try:
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
        finally:
            self.undo_stack.endMacro()
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
        text = self._read_text(path)
        if text is None:
            return 0
        script = read_atlas(text)
        notices = list(script.notices)
        by_pointer = {p.address: r for r in entry.doc.strings for p in r.pointers}
        by_start = {r.start: r for r in entry.doc.strings}
        applied = 0
        self.undo_stack.beginMacro(f"Import {os.path.basename(path)}")
        try:
            for i, item in enumerate(script.strings):
                rec = None
                for addr in item.pointers:
                    rec = by_pointer.get(addr)
                    if rec is not None:
                        break
                if rec is None and item.insert_at is not None:
                    rec = by_start.get(item.insert_at)
                if rec is None:
                    notices.append(f"string {i}: no block string matches its address")
                    continue
                self._set_translation(entry, rec.index, item.text)
                applied += 1
        finally:
            self.undo_stack.endMacro()
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
        export = write_atlas(
            entry.name, entry.config, entry.doc.strings, tables, table_files
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
        parent_doc = self._load_document(entry.parent) if entry.parent else None
        header = int(parent_doc.ctx.get(KEY_HEADER_SIZE, 0) or 0) if parent_doc else 0
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

    def _block_strings_by_name(self, file_entry: Entry | None) -> dict[str, list]:
        out: dict[str, list] = {}
        for e in self.workspace.of_kind(EntryKind.BLOCK):
            if file_entry and e.parent is not file_entry:
                continue
            strings = self._block_strings(e)
            if strings is not None:
                out[e.name] = strings
        return out

    def _import(self, kind: str) -> None:
        filters = {
            "script": "Scripts (*.txt);;All files (*)",
            "delimited": "Tables (*.tsv *.csv);;All files (*)",
            "po": "PO files (*.po);;All files (*)",
        }[kind]
        path = self._pick_open("Import", filters)
        if not path:
            return
        self.import_file(path, kind)

    def import_file(self, path: str, kind: str, force: bool = False) -> None:
        text = self._read_text(path)
        if text is None:
            return
        file_entry = self._current_file()
        blocks = self._block_strings_by_name(file_entry)
        before = {
            name: [(r.translation, r.status, r.notes) for r in strs]
            for name, strs in blocks.items()
        }
        try:
            if kind == "script":
                report = apply_script(parse_script(text, path), blocks)
                notices = list(report.notices)
                for name, cfg in report.new_blocks:
                    if file_entry is not None:
                        entry = Entry(
                            EntryKind.BLOCK,
                            name,
                            file_entry.path,
                            parent=file_entry,
                            config=cfg,
                        )
                        self._push_add(entry)
                        notices.append(f"created block {name}")
                applied = report.applied
            else:
                records = read_po(text) if kind == "po" else read_delimited(text)
                report = apply_records(records, blocks, force=force)
                notices, applied = report.skipped, report.applied
        except (MapcharError, ValueError) as exc:
            self._error(f"Cannot import {path}: {exc}")
            return
        # Record the changes as one undo step.
        self.undo_stack.beginMacro(f"Import {os.path.basename(path)}")
        for name, strs in blocks.items():
            entry = next(
                (
                    e
                    for e in self.workspace.entries
                    if e.kind is EntryKind.BLOCK and e.name == name
                ),
                None,
            )
            if entry is None:
                continue
            for rec, (tr, st, notes) in zip(strs, before[name], strict=False):
                new = (rec.translation, rec.status, rec.notes)
                rec.translation, rec.status, rec.notes = tr, st, notes
                if new[0] != tr:
                    self._push_command(
                        StringFieldCommand(
                            self, entry, rec.index, "translation", tr, new[0]
                        )
                    )
                if new[1] != st:
                    self._push_command(
                        StringFieldCommand(
                            self, entry, rec.index, "status", st.value, new[1].value
                        )
                    )
                if new[2] != notes:
                    self._push_command(
                        StringFieldCommand(
                            self, entry, rec.index, "notes", notes, new[2]
                        )
                    )
        self.undo_stack.endMacro()
        self._remember_dir(path)
        self._refresh_view()
        self._report(
            "Import Notices",
            f"Imported {applied} string(s) from {os.path.basename(path)}",
            notices,
        )

    def _export(self, kind: str) -> None:
        entry = self._current_block(need_doc=True, complain="Select a block to export.")
        if entry is None:
            return
        ext = {"tsv": "tsv", "csv": "csv", "po": "po"}[kind]
        path = self._pick_save("Export", f"{entry.name}.{ext}", f"*.{ext}")
        if not path:
            return
        self.export_file(path, kind)

    def export_file(self, path: str, kind: str) -> None:
        entry = self._entry
        records = records_for(entry.name, entry.doc.strings)
        if kind == "po":
            rom = (
                os.path.basename(entry.parent.path)
                if entry.parent and entry.parent.path
                else "rom"
            )
            text = write_po(records, rom)
        else:
            text = write_delimited(records, "\t" if kind == "tsv" else ",")
        if not self._write_text(path, text):
            return
        self._remember_dir(path)
        self.statusBar().showMessage(
            f"Exported {len(records)} string(s) to {path}", 5000
        )
