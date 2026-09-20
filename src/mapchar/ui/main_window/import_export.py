"""mapChar's own exchange: the script, translator tables and PO files.

The prior-art formats — Cartographer command files and Atlas scripts — are
:mod:`mapchar.ui.main_window.legacy_exchange`.
"""

from __future__ import annotations

import os

from mapchar.core.errors import MapcharError
from mapchar.core.text import same_text
from mapchar.project.formats.script import (
    ScriptImportReport,
    apply_script,
    parse_script,
)
from mapchar.project.formats.summary import (
    ImportSummary,
    summarise_records,
    summarise_script,
)
from mapchar.project.formats.translator import (
    ImportReport,
    apply_records,
    read_delimited,
    read_po,
    records_for,
    write_delimited,
    write_po,
)
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.dialogs import ImportDialog
from mapchar.ui.undo_commands import StringFieldCommand


class ImportExportMixin:
    """The script, translator tables and PO files, in and out.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _block_strings_by_name(self, file_entry: Entry | None) -> dict[str, list]:
        return {
            e.name: doc.strings for e, doc in self._readable_blocks(of_file=file_entry)
        }

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

    def _plan_import(
        self,
        text: str,
        path: str,
        kind: str,
        blocks: dict[str, list],
        force: bool = False,
    ) -> tuple[ScriptImportReport | ImportReport, ImportSummary]:
        """What importing ``text`` would do, and the summary of it.

        Nothing is changed: both importers return what each string is to say
        and leave the saying of it to the caller. That is what lets the same
        call draw the confirmation, re-draw it under **Force**, and then be the
        thing that is applied.
        """
        if kind == "script":
            script = parse_script(text, path)
            report = apply_script(script, blocks)
            return report, summarise_script(script, report)
        records = read_po(text) if kind == "po" else read_delimited(text)
        report = apply_records(records, blocks, force=force)
        return report, summarise_records(
            "PO file" if kind == "po" else "Translator table", report
        )

    def _land_texts(
        self, texts: dict[str, dict[int, str]], label: str, notices: list[str]
    ) -> int:
        """Put imported texts into the blocks' bytes, one undo step in all.

        Each block's texts go in as one edit — a packed block lays every
        string out together, so they must — and a block whose texts will not
        all fit is tried string by string, so that one over-long line does not
        hold the rest of the file back. Whatever is refused is listed in
        ``notices`` and left as it was; how many landed comes back.
        """

        def planned():
            for name, by_index in texts.items():
                entry = self.workspace.block_named(name)
                if entry is None or entry.doc is None:
                    continue
                yield (
                    entry,
                    {
                        i: t
                        for i, t in by_index.items()
                        if (rec := entry.doc.string_by_index(i)) is not None
                        and not same_text(rec.current_text(), t)
                    },
                )

        # The block that was current is put back by the caller, around all of
        # the import rather than around the texts alone.
        landed, problems = self._edit_blocks(
            planned(), label, restore=False, separator="/"
        )
        notices += problems
        return landed

    def import_file(
        self, path: str, kind: str, force: bool = False, confirm: bool = True
    ) -> None:
        """Import ``path``, having shown what that will do and been told to.

        Both importers plan without changing anything, so the plan is what the
        dialog draws and **Force** re-plans rather than being decided in
        advance. Only past the confirmation does any of it land.
        """
        text, read_notices = self._read_text(path)
        if text is None:
            return
        file_entry = self._current_file()
        blocks = self._block_strings_by_name(file_entry)
        try:
            report = self._plan_import(text, path, kind, blocks, force)[0]
        except (MapcharError, ValueError) as exc:
            self._error(f"Cannot import {path}: {exc}")
            return
        if confirm:
            dialog = ImportDialog(
                os.path.basename(path),
                lambda forced: self._plan_import(text, path, kind, blocks, forced)[1],
                self,
            )
            if dialog.exec() != ImportDialog.DialogCode.Accepted:
                return
            if dialog.forced() != force:
                force = dialog.forced()
                report = self._plan_import(text, path, kind, blocks, force)[0]
        review: dict[str, dict[int, bool]] = {}
        done: dict[str, dict[int, bool]] = {}
        notes: dict[str, dict[int, str]] = {}
        label = f"Import {os.path.basename(path)}"
        current = self._entry
        with self._macro(label):
            if kind == "script":
                created = [
                    Entry(
                        EntryKind.BLOCK,
                        name,
                        file_entry.path,
                        parent=file_entry,
                        config=cfg,
                    )
                    for name, cfg in report.new_blocks
                    if file_entry is not None
                ]
                for entry in created:
                    self._push_add(entry)
                if created:
                    # Nothing was placed in those blocks, since they did not
                    # exist when the script was planned. Now that they do, plan
                    # again over them: the summary counted their strings on the
                    # promise that this pass lands them.
                    blocks = self._block_strings_by_name(file_entry)
                    report = self._plan_import(text, path, kind, blocks)[0]
                notices = [f"created block {e.name}" for e in created]
                notices += report.notices
            else:
                notices = list(report.skipped)
                review, notes, done = report.review, report.notes, report.done
            notices = read_notices + notices
            applied = self._land_texts(report.texts, label, notices)
            for name, strs in blocks.items():
                entry = self.workspace.block_named(name)
                if entry is None:
                    continue
                for rec in strs:
                    marked = (
                        "review"
                        if review.get(name, {}).get(rec.index)
                        else "done"
                        if done.get(name, {}).get(rec.index)
                        else None
                    )
                    if marked is not None and rec.status.value != marked:
                        self._push_command(
                            StringFieldCommand(
                                self,
                                entry,
                                rec.index,
                                "status",
                                rec.status.value,
                                marked,
                            )
                        )
                    note = notes.get(name, {}).get(rec.index)
                    if note is not None and note != rec.notes:
                        self._push_command(
                            StringFieldCommand(
                                self, entry, rec.index, "notes", rec.notes, note
                            )
                        )
            if current is not None and self._entry is not current:
                self._activate_entry(current)
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
        entry = self._current_block(need_doc=True, complain="Select a block to export.")
        if entry is None:
            return
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
