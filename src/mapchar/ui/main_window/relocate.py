"""Files a project references that are not on disk: finding them again."""

from __future__ import annotations

import os

from mapchar.core.errors import MapcharError
from mapchar.project.tables import adopt_table, read_table_file
from mapchar.project.workspace import Entry, EntryKind, missing_paths, relocate_path


class RelocateMixin:
    """Files a project references that are not on disk: finding them again.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _read_blocks(self) -> None:
        """Read every block the session has not, so the Files panel counts its
        strings before it is opened.

        A block over a file that is not on disk is left for Locate, and one
        whose file fails to read is tried once, not once per block
        (:meth:`~mapchar.ui.main_window.block_reading.BlockReadingMixin._readable_blocks`).
        """
        # Walked for the reading itself: the records land on the entries.
        list(self._readable_blocks(skip_missing=True))

    def _sync_locate_action(self) -> None:
        """Arm File ▸ Locate Missing Files… only when there is one to locate.

        Re-asked when the File menu opens, which is the moment the answer is
        read; the scan behind it stats every distinct referenced path, cheap
        enough once but not once per row.
        """
        self.locate_action.setEnabled(bool(missing_paths(self.workspace)))

    def _relocate_missing(self, *, prompt_summary: bool = False) -> None:
        """Walk the referenced files that are not on disk, offering to re-point
        each; every entry that shared the old path follows it.

        ``prompt_summary`` opens with one confirmation, for the project-load
        entry point; the menu dives straight into the pickers. A file the user
        skips stays missing, so the menu row stays armed.
        """
        paths = missing_paths(self.workspace)
        if not paths:
            self.statusBar().showMessage("No missing files", 3000)
            return
        if prompt_summary:
            shown = [os.path.basename(p) for p in paths[:6]]
            if len(paths) > len(shown):
                shown.append(f"…and {len(paths) - len(shown)} more")
            if not self._ask(
                "Missing Files",
                f"This project references {len(paths)} file(s) that could not "
                "be found. Locate them now?\n\n" + "\n".join(shown),
            ):
                return
        relocated = 0
        for old in paths:
            new = self._pick_open(f"Locate {os.path.basename(old)}")
            if not new:
                continue  # skipped — it stays missing
            clash = self.workspace.find_file(new)
            if self.workspace.find_file(old) is not None and clash is not None:
                self._error(
                    f"{os.path.basename(new)} is already open in this project, so "
                    f"{os.path.basename(old)} cannot be relocated onto it."
                )
                continue
            for entry in relocate_path(self.workspace, old, new):
                self._refresh_relocated(entry)
            relocated += 1
        self._sync_locate_action()
        self._refresh_table_picks()
        self._read_blocks()
        self.files_panel.rebuild()
        self._activate_entry(self.workspace.current)
        remaining = len(missing_paths(self.workspace))
        self.statusBar().showMessage(
            f"Relocated {relocated} file(s)"
            + (f"; {remaining} still missing." if remaining else "."),
            5000,
        )

    def _refresh_relocated(self, entry: Entry) -> None:
        """Re-read one entry whose path was just corrected.

        The cached document described the file that was not there, so it goes —
        keeping the block's translations, which live on the entry once the
        document is dropped. A table is re-read straight away, because every
        block reading it needs its tokens before it can be shown at all.
        """
        entry.missing = False
        if entry is self._entry:
            self._entry = None
            self._doc = None
        self.workspace.drop_document(entry)
        if entry.kind is EntryKind.TABLE and entry.path:
            try:
                tf = read_table_file(entry.path, entry.dialect, self.registry)
                adopt_table(entry, tf.table, tf.notices)
                entry.dialect = tf.dialect
            except (OSError, MapcharError) as exc:
                entry.missing = True
                self._error(f"{entry.name}: {exc}")
