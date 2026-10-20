"""mapChar's own exchange: the script, translator tables and PO files.

The prior-art formats — Cartographer command files and Atlas scripts — are
:mod:`mapchar.ui.main_window.legacy_exchange`.
"""

from __future__ import annotations

import os

from mapchar.core.errors import MapcharError
from mapchar.core.text import same_text
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.formats.script import (
    Script,
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
    Record,
    apply_records,
    merge_reports,
    read_delimited,
    read_po,
    records_for,
    write_delimited,
    write_po,
)
from mapchar.ui.dialogs import ImportDialog
from mapchar.ui.undo_commands import StringFieldCommand

_FILTERS = {
    "script": "Scripts (*.txt);;All files (*)",
    "delimited": "Tables (*.tsv *.csv);;All files (*)",
    "po": "PO files (*.po);;All files (*)",
}
_KIND_NAMES = {"delimited": "Translator table", "po": "PO file"}
"""What the import dialog calls each translator format."""


class ImportExportMixin:
    """The script, translator tables and PO files, in and out.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _block_strings_by_name(self, file_entry: Entry | None) -> dict[str, list]:
        """The strings of every readable block over ``file_entry`` by block
        name, or of every readable block in the project with none."""
        return {
            e.name: doc.strings for e, doc in self._readable_blocks(of_file=file_entry)
        }

    def _import(self, kind: str, *, whole_project: bool = False) -> None:
        # A script is one file: it creates the blocks it names and is planned
        # again over them, and a project has one. The translator formats take
        # any number, imported together.
        if kind == "script":
            path = self._pick_open("Import", _FILTERS[kind])
            paths = [path] if path else []
        else:
            paths = self._pick_open_many("Import", _FILTERS[kind])
        if paths:
            self.import_files(paths, kind, whole_project=whole_project)

    def _import_block(self, kind: str) -> None:
        """One translator file over the current block alone — the Files
        panel's **Import ▸** on a block's row."""
        entry = self._current_block(
            need_doc=True, complain="Select a block to import into."
        )
        if entry is None:
            return
        path = self._pick_open(f"Import into {entry.name}", _FILTERS[kind])
        if path:
            self.import_file(path, kind, only=entry)

    @staticmethod
    def _parse(text: str, path: str, kind: str) -> Script | list[Record]:
        """The script or the records ``text`` holds; raises what stops it."""
        if kind == "script":
            return parse_script(text, path)
        return read_po(text) if kind == "po" else read_delimited(text)

    @staticmethod
    def _plan_import(
        parsed: list[tuple[str, Script | list[Record]]],
        kind: str,
        blocks: dict[str, list],
        force: bool = False,
    ) -> tuple[ScriptImportReport | ImportReport, ImportSummary]:
        """What importing the ``(file name, parsed)`` files would do, and the
        summary of it.

        Nothing is changed: both importers return what each string is to say
        and leave the saying of it to the caller. That is what lets the same
        call draw the confirmation, re-draw it under **Force**, and then be the
        thing that is applied. Several translator files are each planned over
        the same blocks and merged, a later file winning a string an earlier
        one also reaches (:func:`~mapchar.project.formats.translator.
        merge_reports`).
        """
        if kind == "script":
            ((_, script),) = parsed
            report = apply_script(script, blocks)
            return report, summarise_script(script, report)
        report = merge_reports(
            [
                (name, apply_records(records, blocks, force=force))
                for name, records in parsed
            ]
        )
        return report, summarise_records(_KIND_NAMES[kind], report)

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
        self,
        path: str,
        kind: str,
        force: bool = False,
        confirm: bool = True,
        *,
        whole_project: bool = False,
        only: Entry | None = None,
    ) -> None:
        """Import ``path``: :meth:`import_files` with the one file."""
        self.import_files(
            [path], kind, force, confirm, whole_project=whole_project, only=only
        )

    def import_files(
        self,
        paths: list[str],
        kind: str,
        force: bool = False,
        confirm: bool = True,
        *,
        whole_project: bool = False,
        only: Entry | None = None,
    ) -> None:
        """Import ``paths``, having shown what that will do and been told to.

        Both importers plan without changing anything, so the plan is what the
        dialog draws and **Force** re-plans rather than being decided in
        advance. Only past the confirmation does any of it land.

        The records are read against the current file's blocks; against
        every block of the project with ``whole_project`` — a translator file
        only, since a script creates its blocks in the current file; or
        against the one block ``only``, read already, so that a record naming
        any other block is skipped and listed.

        Several files are one import: each is read and parsed before anything
        is planned, so one that cannot be stops them all, and one
        confirmation, one undo step and one report cover the lot. A script is
        only ever one file.
        """
        if not paths:
            return
        parsed: list[tuple[str, Script | list[Record]]] = []
        read_notices: list[str] = []
        for path in paths:
            text, notices = self._read_text(path)
            if text is None:
                return
            try:
                parsed.append((os.path.basename(path), self._parse(text, path, kind)))
            except (MapcharError, ValueError) as exc:
                self._error(f"Cannot import {path}: {exc}")
                return
            read_notices += notices
        file_entry = self._current_file()
        if only is not None:
            blocks = {only.name: only.doc.strings}
        else:
            blocks = self._block_strings_by_name(None if whole_project else file_entry)
        report = self._plan_import(parsed, kind, blocks, force)[0]
        what = parsed[0][0] if len(parsed) == 1 else f"{len(parsed)} files"
        if confirm:
            dialog = ImportDialog(
                what,
                lambda forced: self._plan_import(parsed, kind, blocks, forced)[1],
                self,
            )
            if dialog.exec() != ImportDialog.DialogCode.Accepted:
                return
            if dialog.forced() != force:
                force = dialog.forced()
                report = self._plan_import(parsed, kind, blocks, force)[0]
        review: dict[str, dict[int, bool]] = {}
        done: dict[str, dict[int, bool]] = {}
        notes: dict[str, dict[int, str]] = {}
        label = f"Import {what}"
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
                    report = self._plan_import(parsed, kind, blocks)[0]
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
        self._remember_dir(paths[0])
        self._refresh_view()
        self._report(
            "Import Notices",
            f"Imported {applied} string(s) from {what}",
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
        records = records_for(entry.name, entry.doc.strings, self._rom_name(entry))
        if not self._write_records(path, kind, records):
            return
        self.statusBar().showMessage(
            f"Exported {len(records)} string(s) to {path}", 5000
        )

    def _export_all(self, kind: str) -> None:
        stem = (
            os.path.splitext(os.path.basename(self.project_path))[0]
            if self.project_path
            else "strings"
        )
        path = self._pick_save("Export All Blocks", f"{stem}.{kind}", f"*.{kind}")
        if not path:
            return
        self.export_all_file(path, kind)

    def export_all_file(self, path: str, kind: str) -> None:
        """Every readable block's strings into one file, in project order.

        The ids name the blocks, so the file imports back over the whole
        project. A block that has a configuration and cannot be read is left
        out and named once the file is written.
        """
        records: list[Record] = []
        read: set[int] = set()
        for block, doc in self._readable_blocks():
            read.add(id(block))
            records += records_for(block.name, doc.strings, self._rom_name(block))
        if not read:
            self._error("The project has no blocks to export.")
            return
        left_out = [
            f"{block.name}: cannot be read, so it is not in the file"
            for block in self.workspace.of_kind(EntryKind.BLOCK)
            if block.config is not None and id(block) not in read
        ]
        if not self._write_records(path, kind, records):
            return
        self._report(
            "Export Notices",
            f"Exported {len(records)} string(s) of {len(read)} block(s) to {path}",
            left_out,
        )

    @staticmethod
    def _rom_name(block: Entry) -> str:
        """What a PO reference calls the file ``block`` reads."""
        parent = block.parent
        return os.path.basename(parent.path) if parent and parent.path else "rom"

    def _write_records(self, path: str, kind: str, records: list[Record]) -> bool:
        """Write ``records`` to ``path`` as TSV, CSV or PO; ``False`` once what
        stopped it is reported."""
        if kind == "po":
            text = write_po(records)
        else:
            text = write_delimited(records, "\t" if kind == "tsv" else ",")
        if not self._write_text(path, text):
            return False
        self._remember_dir(path)
        return True
