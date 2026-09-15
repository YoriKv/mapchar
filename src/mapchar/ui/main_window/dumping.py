"""File ▸ Dump: a block's strings as text."""

from __future__ import annotations

import os

from mapchar.project.formats.script import write_script
from mapchar.project.workspace import EntryKind
from mapchar.ui.dialogs import DumpDialog


class DumpingMixin:
    """File ▸ Dump: a block's strings as text.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _dump(self, all_blocks: bool = False) -> None:
        # Ctrl+D duplicates the selected row while the Files panel has the
        # keyboard, the way Ctrl+F becomes the filter there.
        if not all_blocks and self.files_panel.has_focus():
            self._duplicate_selection()
            return
        file_entry = self._current_file()
        if file_entry is None:
            self._error("Open a ROM and create a block first.")
            return
        blocks = [
            e for e in self.workspace.children(file_entry) if e.kind is EntryKind.BLOCK
        ]
        current = self._current_block()
        if not all_blocks and current is not None:
            blocks = [current]
        if not blocks:
            self._error("The file has no blocks.")
            return
        dialog = DumpDialog(self)
        dialog.all_blocks.setChecked(all_blocks)
        dialog.all_blocks.setEnabled(
            not all_blocks
            and len(blocks) == 1
            and len(self.workspace.children(file_entry)) > 1
        )
        if dialog.exec() != DumpDialog.DialogCode.Accepted:
            return
        if dialog.all_blocks.isChecked():
            blocks = [
                e
                for e in self.workspace.children(file_entry)
                if e.kind is EntryKind.BLOCK
            ]
        name = blocks[0].name if len(blocks) == 1 else file_entry.name
        path = self._pick_save(
            "Dump to Script", f"{name}.txt", "Scripts (*.txt);;All files (*)"
        )
        if not path:
            return
        payload = []
        for block in blocks:
            strings = self._block_strings(block)
            if strings is None:
                continue
            payload.append((block.name, block.config, strings))
        table_paths = [
            os.path.relpath(e.path, os.path.dirname(path))
            for e in self.workspace.table_entries()
            if e.path
        ]
        text = write_script(
            payload,
            dialog.dump_mode(),
            rom=os.path.relpath(file_entry.path, os.path.dirname(path))
            if file_entry.path
            else None,
            tables=table_paths,
        )
        if not self._write_text(path, text):
            return
        self._remember_dir(path)
        self.statusBar().showMessage(
            f"Dumped {sum(len(p[2]) for p in payload)} strings to {path}", 5000
        )
