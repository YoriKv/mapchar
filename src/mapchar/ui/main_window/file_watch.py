"""ROM files on disk: noticing another program change one, and reading it again."""

from __future__ import annotations

import hashlib
import os

from mapchar.core.errors import MapcharError
from mapchar.pipeline.filechange import edit_runs, on_disk, replay
from mapchar.pipeline.pipeline import FileRef, PathwayConfig, load
from mapchar.project.entry import Entry, EntryKind, normalize_path

CHANGE_REST_MS = 300
"""How long after the last change signal a file is looked at: a program
writes a ROM in several passes, and each is a signal."""


class FileWatchMixin:
    """ROM files on disk: noticing another program change one, and reading it
    again.

    ``file_watcher`` holds every open file's paths. A signal only says the
    file was touched: what decides anything is the bytes, read once the
    signals rest and told against the document's ``raw`` — what the load read,
    or what the last write left — so a touch that changed nothing, and the
    app's own write, ask nothing. A file that did change is offered a reload,
    and the edits made here are laid back over what was read.

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
        for path in gone:
            self._declined_disk.pop(normalize_path(path), None)

    def _rewatch_files(self) -> None:
        paths = self.file_watcher.files()
        if paths:
            self.file_watcher.removePaths(paths)
        self._changed_paths.clear()
        self._declined_disk.clear()
        for entry in self.workspace.files():
            self._watch_file(entry)

    def _on_file_changed(self, path: str) -> None:
        """The watcher's signal: noted, and looked at once the signals rest."""
        self._changed_paths.add(path)
        self._file_change_rest.start()

    def _check_changed_files(self) -> None:
        """Look at every file the watcher reported, one prompt at a time.

        A prompt runs an event loop of its own, and a signal that lands during
        it joins the set the loop below is draining rather than starting a
        second drain over the same files.
        """
        if self._checking_files:
            return
        self._checking_files = True
        try:
            while self._changed_paths:
                self._check_changed_file(self._changed_paths.pop())
        finally:
            self._checking_files = False

    def _check_changed_file(self, path: str) -> None:
        # A program that replaces the file rather than writing over it leaves
        # the watcher holding a path that is gone: taken up again here.
        if os.path.exists(path) and path not in self.file_watcher.files():
            self.file_watcher.addPath(path)
        key = normalize_path(path)
        for entry in self.workspace.files():
            if any(normalize_path(p) == key for p in entry.paths):
                self._offer_reload(entry)

    def _offer_reload(self, entry: Entry) -> None:
        """Ask to reload ``entry`` if its files no longer hold what it read.

        An entry with no document has nothing to go stale: its next load reads
        the disk. A reload the user declined is not asked for again while the
        files hold what they held then — the next change asks afresh.
        """
        doc = entry.doc
        if doc is None:
            return
        held = on_disk(entry.paths)
        if held == doc.raw:
            return
        digest = hashlib.sha1(held).digest()
        key = normalize_path(entry.path)
        if self._declined_disk.get(key) == digest:
            return
        if self._has_edits(entry):
            question = (
                f"{entry.name} changed on disk and has edits here that are not "
                "written yet. Reload it? Your edits stay on top of what is "
                "reloaded, and win where they overlap."
            )
        else:
            question = f"{entry.name} changed on disk. Reload it?"
        if not self._ask("File Changed on Disk", question):
            self._declined_disk[key] = digest
            return
        self.reload_file(entry)

    def _has_edits(self, entry: Entry) -> bool:
        """Whether the file, or a block over it, holds edits not yet written."""
        return entry.dirty or any(b.dirty for b in self.workspace.blocks_of(entry))

    def reload_file(self, entry: Entry) -> None:
        """Read ``entry``'s files again, keeping the edits made here.

        The edits are the runs in which the buffer differs from what its
        ``raw`` decodes to, and they are laid over the payload the new bytes
        decode to, winning where the two overlap. Every plain block reads the
        same buffer and is given it; a compressed block with no edits is
        dropped and decompresses again from the new bytes when next shown,
        and one with edits keeps its payload, which is where they live. The
        revision tokens stay: what was unsaved is unsaved still, being laid
        over bytes that never held it, and what was clean now matches the disk.
        """
        doc = entry.doc
        if doc is None or entry.kind is not EntryKind.FILE:
            return
        cfg = PathwayConfig(
            FileRef(entry.paths), entry.container_id, entry.compression_id
        )
        try:
            fresh = load(cfg, self.registry)
            base = load(
                PathwayConfig(
                    FileRef(entry.paths, data=doc.raw),
                    entry.container_id,
                    entry.compression_id,
                ),
                self.registry,
            ).data
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot reload {entry.name}: {exc}")
            return
        data = replay(edit_runs(base, doc.data), fresh.data)
        doc.data = data
        doc.raw = fresh.raw
        doc.ctx = fresh.ctx
        doc.writable = fresh.writable
        doc.missing_plugins = fresh.missing_plugins
        doc.extraction_key = None
        self._declined_disk.pop(normalize_path(entry.path), None)
        for child in self.workspace.blocks_of(entry, loaded=True):
            if not child.compression_id:
                child.doc.data = data
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
        self.statusBar().showMessage(f"Reloaded {entry.name}", 4000)
