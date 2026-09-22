"""ROM files on disk: noticing another program change one, and reading it again."""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from mapchar.core.errors import MapcharError
from mapchar.pipeline.filechange import Merge, merge
from mapchar.pipeline.pipeline import FileRef, PathwayConfig, load
from mapchar.project.entry import Entry, EntryKind, normalize_path

CHANGE_REST_MS = 300
"""How long after the last change signal the files are looked at: a program
writes a ROM in several passes, and each is a signal."""


class FileWatchMixin:
    """ROM files on disk: noticing another program change one, and reading it
    again.

    ``file_watcher`` holds every open file's paths. A signal only says the
    file was touched: what decides anything is the window's ``DiskState``,
    which knows each file's modification time and a digest of its bytes as
    they were last read, written or declined here — so a touch that changed
    nothing, and the app's own writes, ask nothing. The files that did change
    are offered as one reload, and the edits made here are laid back over
    what was read, byte for byte.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _watch_file(self, entry: Entry) -> None:
        if entry.kind is not EntryKind.FILE:
            return
        watched = set(self.file_watcher.files())
        for path in entry.paths:
            if path not in watched and os.path.exists(path):
                self.file_watcher.addPath(path)

    def _unwatch_file(self, entry: Entry) -> None:
        if entry.kind is not EntryKind.FILE:
            return
        gone = [p for p in entry.paths if p in self.file_watcher.files()]
        if gone:
            self.file_watcher.removePaths(gone)
        self._disk_state.forget(entry.paths)

    def _rewatch_files(self) -> None:
        paths = self.file_watcher.files()
        if paths:
            self.file_watcher.removePaths(paths)
        self._changed_paths.clear()
        files = self.workspace.files()
        self._disk_state.retain(tuple(p for e in files for p in e.paths))
        for entry in files:
            self._watch_file(entry)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Coming back to the window is the moment to look at every open file,
        once: for the write the watcher cannot see — made from the other side
        of a WSL mount, or a network share — and the path it dropped when a
        program saved by rename. Deferred out of the activation itself, since
        a modal shown from inside the event that made the window active is
        shown to a window Qt has not finished activating.
        """
        super().changeEvent(event)
        if event.type() is QEvent.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(0, self._look_at_files)

    def _look_at_files(self) -> None:
        """One stat per open file — no timer — and the check a signal gets,
        which takes up again any path the watcher dropped."""
        for entry in self.workspace.files():
            self._changed_paths.update(entry.paths)
        self._check_changed_files()

    def _on_file_changed(self, path: str) -> None:
        """The watcher's signal: noted, and looked at once the signals rest."""
        self._changed_paths.add(path)
        self._file_change_rest.start()

    def _check_changed_files(self) -> None:
        """Look at the files the watcher reported, and offer the changed ones.

        Not while another dialog is up — a file picker, a write's own report —
        which would put the question under it: the files stay noted and the
        rest timer asks again. The offer's own dialog runs an event loop of its
        own, and a signal that lands during it joins the set the loop below is
        draining rather than starting a second drain over the same files.
        """
        if self._checking_files:
            return
        if QApplication.activeModalWidget() is not None:
            self._file_change_rest.start()
            return
        self._checking_files = True
        try:
            while self._changed_paths:
                paths, self._changed_paths = self._changed_paths, set()
                self._offer_reload(self._changed_entries(paths))
        finally:
            self._checking_files = False

    def _changed_entries(self, paths: set[str]) -> list[Entry]:
        """The loaded file entries whose files hold other bytes than they read.

        A file that is not there — mid-rename, or deleted — is no change, and
        neither is one never loaded, whose next load reads the disk anyway.
        """
        changed: list[Entry] = []
        for path in sorted(paths):
            # A program that replaces the file rather than writing over it
            # leaves the watcher holding a path that is gone: taken up again.
            if os.path.exists(path) and path not in self.file_watcher.files():
                self.file_watcher.addPath(path)
            if not self._disk_state.changed(path):
                continue
            key = normalize_path(path)
            for entry in self.workspace.files():
                if entry.doc is None or entry in changed:
                    continue
                if any(normalize_path(p) == key for p in entry.paths):
                    changed.append(entry)
        return changed

    def _offer_reload(self, entries: list[Entry]) -> None:
        """Put the changed files to the user as one question.

        Declined, each file as it now stands is what "unchanged" means from
        here on, or the same question would come back with the next signal;
        File ▸ Reload from Disk is the way back to it.
        """
        if not entries:
            return
        names = [e.name for e in entries]
        unsaved = [e.name for e in entries if self._has_edits(e)]
        if not self._ask_reload(names, unsaved):
            for entry in entries:
                self._disk_state.record(entry.paths)
            return
        merges = [self.reload_file(entry) for entry in entries]
        self._report_reload(names, merges)

    def _ask_reload(self, names: list[str], unsaved: list[str]) -> bool:
        """The prompt; ``True`` to reload. Its own box rather than :meth:`_ask`,
        since "No" is the wrong name for keeping the bytes in memory."""
        if len(names) == 1:
            text = f"{names[0]} was changed on disk by another program."
        else:
            listed = "\n".join(f"• {name}" for name in names)
            text = (
                f"{len(names)} files were changed on disk by another program:\n{listed}"
            )
        text += "\n\nReload from disk?"
        if unsaved:
            text += (
                f"\n\nThe edits made here that are not written yet "
                f"({', '.join(unsaved)}) are kept: they are put back over the "
                "new contents, byte for byte."
            )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("File Changed on Disk")
        box.setText(text)
        reload = box.addButton("Reload", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Keep In Memory", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(reload)
        box.exec()
        return box.clickedButton() is reload

    def _has_edits(self, entry: Entry) -> bool:
        """Whether the file, or a block over it, holds edits not yet written."""
        return entry.dirty or any(b.dirty for b in self.workspace.blocks_of(entry))

    def _reload_current_file(self) -> None:
        """File ▸ Reload from Disk: the file on screen, asked for outright.

        For the change the watcher never sees — a program that replaces the
        file rather than writing over it, a filesystem it cannot follow — and
        for a reload declined and wanted after all. A file that holds what it
        held costs a read and nothing else, and says so.
        """
        entry = self._current_file()
        if entry is None:
            self.statusBar().showMessage("Nothing to reload", 3000)
            return
        if entry.doc is None and self._load_document(entry) is None:
            return
        merged = self.reload_file(entry)
        if merged is not None:
            self._report_reload([entry.name], [merged])

    def reload_file(self, entry: Entry) -> Merge | None:
        """Read ``entry``'s files again, keeping the edits made here.

        The edits are the bytes in which the buffer differs from its
        :attr:`~mapchar.core.document.Document.base`, and they are laid over
        the payload the new bytes decode to, winning where the two overlap
        (:func:`~mapchar.pipeline.filechange.merge`). Every plain block reads
        the same buffer and is given it; a compressed block with no edits is
        dropped and decompresses again from the new bytes when next shown,
        and one with edits keeps its payload, which is where they live. The
        revision tokens stay: what was unsaved is unsaved still, being laid
        over bytes that never held it, and what was clean now matches the disk.

        The merge, or ``None`` when the file could not be read, or holds what
        it held — either is said, and the document stays as it was.
        """
        doc = entry.doc
        if doc is None or entry.kind is not EntryKind.FILE:
            return None
        cfg = PathwayConfig(
            FileRef(entry.paths), entry.container_id, entry.compression_id
        )
        try:
            fresh = load(cfg, self.registry)
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot reload {entry.name}: {exc}")
            return None
        self._disk_state.record(entry.paths)
        if fresh.data == doc.base and fresh.raw == doc.raw:
            self.statusBar().showMessage(f"{entry.name} is up to date", 4000)
            return None
        merged = merge(doc.base, doc.data, fresh.data)
        doc.data = merged.data
        doc.base = fresh.data
        doc.raw = fresh.raw
        doc.ctx = fresh.ctx
        doc.writable = fresh.writable
        doc.missing_plugins = fresh.missing_plugins
        doc.extraction_key = None
        for child in self.workspace.blocks_of(entry, loaded=True):
            if not child.compression_id:
                child.doc.data = merged.data
                child.doc.extraction_key = None
            elif not child.dirty:
                self.workspace.drop_document(child)
        for path in entry.paths:
            self.workspace.invalidate_path(path, keep=entry)
        # Whatever reading an edit had checked was of bytes now replaced.
        self._checked_extraction = None
        on_screen = self._entry
        if on_screen is not None and (on_screen is entry or on_screen.parent is entry):
            self._reload_current_document()
        else:
            self._refresh_view()
        self.files_panel.refresh_labels()
        self._refresh_project_strings()
        return merged

    def _report_reload(self, names: list[str], merges: list[Merge | None]) -> None:
        """Say what the reload kept, in the status bar."""
        done = [m for m in merges if m is not None]
        if not done:
            return
        what = names[0] if len(names) == 1 else f"{len(names)} files"
        message = f"Reloaded {what}"
        kept = sum(m.kept for m in done)
        conflicts = sum(m.conflicts for m in done)
        dropped = sum(m.dropped for m in done)
        if kept or dropped:
            message += f", {kept} edited byte{'' if kept == 1 else 's'} kept"
            notes = []
            if conflicts:
                notes.append(f"{conflicts} changed on disk too")
            if dropped:
                notes.append(f"{dropped} past the new end of the file dropped")
            if notes:
                message += f" ({'; '.join(notes)})"
        self.statusBar().showMessage(message, 6000)
