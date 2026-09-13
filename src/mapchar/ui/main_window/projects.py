"""The .mapchar project: opening it, saving it, and what it is missing."""

from __future__ import annotations

import os

from PySide6.QtWidgets import QMessageBox

from mapchar import APP_NAME
from mapchar.core.errors import MapcharError
from mapchar.project.projectfile import (
    PROJECT_VERSION,
    LoadedProject,
    ProjectError,
    load_project,
    project_dict,
    save_project,
)
from mapchar.project.tables import adopt_tables, read_table_file
from mapchar.project.workspace import Entry, EntryKind, missing_paths, relocate_path
from mapchar.ui.dialogs import TextDialog

MAX_RECENT = 10
"""Projects kept in Open Recent."""


class ProjectMixin:
    """The .mapchar project: opening it, saving it, and what it is missing.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _project_dirty(self) -> bool:
        """True when the open project differs from what is on disk.

        A session that has never been saved as a project is **not** dirty: there
        is no file for it to differ from, and prompting to save one would promise
        a project the user never asked for. Opening files is how a session
        starts, not an unsaved change.
        """
        if self._saved_snapshot is None:
            return False
        return self._snapshot() != self._saved_snapshot

    def _snapshot(self) -> str:
        import json

        self._capture_session()
        base = os.path.dirname(self.project_path) if self.project_path else None
        d = project_dict(self.workspace.entries, None, base)
        return json.dumps(d, sort_keys=True)

    def _resolve_dirty_entries(
        self,
        consequence: str,
        *,
        write_label: str = "Write All",
        skip_label: str = "Continue Without",
    ) -> bool:
        """Unsaved-file-edits gate; True when it is OK to go ahead.

        The one prompt for "there are edits in memory that are not on disk":
        write them (Accept), go ahead without doing so (Destructive), or cancel
        the action. What the middle option means is the caller's — saving or
        loading a project *keeps* the edits in memory, quitting drops them — so
        the caller labels it. A project stores references, not bytes, which is
        why saving one has to resolve them first.
        """
        dirty = self._writable_dirty()
        if not dirty:
            return True
        names = ", ".join(e.name for e in dirty)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved Edits")
        box.setText(f"{consequence} ({names}). Write them to disk first?")
        write = box.addButton(write_label, QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton(skip_label, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is write:
            # A write that failed left its entry dirty — don't go past it.
            return self._write_all() and not self._writable_dirty()
        return clicked is skip

    def _writable_dirty(self) -> list[Entry]:
        """The unsaved entries **Write All** can actually resolve.

        Files and the blocks over them; a table's edits go out through Save
        Table, so gating a project save on them would be a prompt with no
        answer.
        """
        return [
            e
            for e in self.workspace.dirty_entries()
            if e.kind in (EntryKind.FILE, EntryKind.BLOCK)
        ]

    def _confirm_discard(self, what: str) -> bool:
        """Both gates in front of anything that abandons the session.

        **The project first.** Saving it runs the file-edits gate of its own
        (a project stores references, not bytes), so asking the other way round
        would ask about the same files twice. The file gate's middle option says
        "Discard" here rather than "Continue Without": past this point the edits
        are not kept in memory either.
        """
        if self._project_dirty():
            answer = QMessageBox.question(
                self,
                "Unsaved Project",
                f"Save the project before you {what}?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Save:
                if not self._save_project():
                    return False
            elif answer != QMessageBox.StandardButton.Discard:
                return False
        return self._resolve_dirty_entries(
            f"Unsaved edits are lost when you {what}",
            skip_label="Discard",
        )

    def _new_project(self) -> None:
        if not self._confirm_discard("start a new project"):
            return
        self._entry = None
        self._doc = None
        self._load_project_plugins(None)  # drop the old project's plugins/ folder
        self.workspace.replace([], None)
        self.undo_stack.clear()
        self._forget_all_visits()  # nothing the trail named survives the swap
        self.project_path = None
        self._saved_snapshot = None
        self._sync_locate_action()
        self._refresh_table_picks()
        self._refresh_view()

    def _open_project_dialog(self) -> None:
        path = self._pick_open("Open Project", f"{APP_NAME} projects (*.mapchar)")
        if path:
            self.open_project(path)

    def open_project(self, path: str) -> bool:
        if not self._confirm_discard("open another project"):
            return False
        try:
            loaded: LoadedProject = load_project(path)
        except ProjectError as exc:
            self._error(str(exc))
            return False
        self._entry = None
        self._doc = None
        self._load_project_plugins(path)  # its plugins/ folder, before anything reads
        for e in loaded.entries:
            if e.kind is not EntryKind.TABLE:
                continue
            if not e.path:
                # No file behind it: the overlay is the whole table.
                adopt_tables(e, [])
                continue
            try:
                tf = read_table_file(e.path, e.dialect, self.registry)
                adopt_tables(e, tf.tables, tf.notices)
                e.dialect = tf.dialect
            except (OSError, MapcharError) as exc:
                e.missing = True
                loaded.warnings.append(f"{e.name}: {exc}")
        self.workspace.replace(loaded.entries, loaded.current)
        for e in loaded.entries:
            # A block whose translations are not on disk yet is an unsaved
            # entry the same as one edited this session: Write All, the file's
            # Write and the Files panel's mark all have to see it.
            if e.kind is EntryKind.BLOCK and any(
                st.translation is not None for st in (e.pending_strings or {}).values()
            ):
                self.workspace.stamp(e)
        self.undo_stack.clear()
        self._forget_all_visits()  # nothing the trail named survives the swap
        self.project_path = path
        self._remember_dir(path)
        self._add_recent(path)
        self._refresh_table_picks()
        if loaded.warnings:
            TextDialog("Project Notices", "\n".join(loaded.warnings), self).exec()
        self._activate_entry(
            loaded.current
            or (self.workspace.files()[0] if self.workspace.files() else None)
        )
        # The baseline only *after* activation has settled: showing the restored
        # entry runs its session through the live widgets, which legitimately
        # clamps an offset past a shortened file. Snapshotting before that leaves
        # the project reading unsaved the instant it opened.
        self._saved_snapshot = self._snapshot()
        self._update_title()
        upgraded = (
            ""
            if loaded.migrated_from is None
            else f" Upgraded from format version {loaded.migrated_from}"
            f"; saving writes version {PROJECT_VERSION}."
        )
        self.statusBar().showMessage(
            f"Loaded {os.path.basename(path)} "
            f"({len(self.workspace.entries)} entries).{upgraded}",
            5000,
        )
        # Referenced files may have moved since the project was saved: arm the
        # menu row, and offer the walk straight away.
        missing = missing_paths(self.workspace)
        self.locate_action.setEnabled(bool(missing))
        if missing:
            self._relocate_missing(prompt_summary=True)
        return True

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
            if (
                QMessageBox.question(
                    self,
                    "Missing Files",
                    f"This project references {len(paths)} file(s) that could not "
                    "be found. Locate them now?\n\n" + "\n".join(shown),
                )
                != QMessageBox.StandardButton.Yes
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
                adopt_tables(entry, tf.tables, tf.notices)
                entry.dialect = tf.dialect
            except (OSError, MapcharError) as exc:
                entry.missing = True
                self._error(f"{entry.name}: {exc}")

    def _save_project(self) -> bool:
        if not self.project_path:
            return self._save_project_as()
        return self._write_project(self.project_path)

    def _save_project_as(self) -> bool:
        path = self._pick_save(
            "Save Project",
            self.project_path or "project.mapchar",
            f"{APP_NAME} projects (*.mapchar)",
        )
        if not path:
            return False
        return self._write_project(path)

    def _write_project(self, path: str) -> bool:
        if not self._resolve_dirty_entries(
            "A project stores file references, not bytes, so it cannot hold the "
            "unsaved edits"
        ):
            return False
        self._capture_session()  # the on-screen entry's session must be current
        try:
            save_project(path, self.workspace.entries, self.workspace.current)
        except OSError as exc:
            self._error(f"Cannot save project: {exc}")
            return False
        self.project_path = path
        self._saved_snapshot = self._snapshot()
        self._add_recent(path)
        self._remember_dir(path)
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 4000)
        return True

    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        if isinstance(value, str):
            # A one-item list comes back out of QSettings as a bare string: the
            # INI backend cannot tell a single element from a scalar.
            value = [value]
        return [str(v) for v in value or []]

    @staticmethod
    def _recent_key(path: str) -> str:
        """What a recent entry is de-duplicated by: separators normalised, and
        case folded where the file system folds it.

        The same project reaches us spelled differently depending on how it was
        opened, and a list that stores both spellings grows a second row for a
        project the user only has one of.
        """
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _add_recent(self, path: str) -> None:
        key = self._recent_key(path)
        recent = [p for p in self._recent() if self._recent_key(p) != key]
        recent.insert(0, os.path.normpath(os.path.abspath(path)))
        self.settings.setValue("recent", recent[:MAX_RECENT])
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        """Fill File ▸ Open Recent, pruning rows whose project has gone.

        Rebuilt each time the File menu opens rather than once, because the list
        outlives the files it names: a project deleted, renamed or on an unmounted
        drive is a dead row the user has no other way to be rid of.
        """
        self.recent_menu.clear()
        recent = self._recent()
        alive = [p for p in recent if os.path.exists(p)]
        if alive != recent:
            self.settings.setValue("recent", alive)
        for path in alive:
            # The name is shown, not the path: a menu row is not a place to read
            # a path out of, and "&" in one would be eaten as a mnemonic marker.
            label = os.path.basename(path).replace("&", "&&")
            action = self.recent_menu.addAction(
                label, lambda p=path: self.open_project(p)
            )
            action.setToolTip(path)
        if alive:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear List", self._clear_recent)
        self.recent_menu.setEnabled(bool(alive))

    def _clear_recent(self) -> None:
        self.settings.remove("recent")
        self._rebuild_recent()
