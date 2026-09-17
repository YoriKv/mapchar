"""Autosave: a copy of the project written on a timer and after every write,
and offered back when a session ended without a save."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QTimer

from mapchar.project.projectfile import ProjectError, load_project, save_project

AUTOSAVE_SUFFIX = ".autosave"
AUTOSAVE_SECONDS = 120


class AutosaveMixin:
    """Autosave: a copy of the project written on a timer and after every
    write, and offered back when a session ended without a save.

    The project is what remembers each string's original; once a ROM has been
    written the bytes no longer do. So the copy goes out beside the project
    file (``name.mapchar.autosave``), or under the application's data folder
    for a session that has no project file yet, and a write to disk saves one
    at once rather than waiting for the timer. Saving the project removes it,
    and so does a normal quit.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _start_autosave(self) -> None:
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_SECONDS * 1000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()
        self._autosaved_snapshot: str | None = None

    def _autosave_path(self) -> str | None:
        """Where this session's copy goes: beside the project, or in the data
        folder for a session with no project file. ``None`` when there is no
        data folder either (a test window)."""
        if self.project_path:
            return self.project_path + AUTOSAVE_SUFFIX
        if not self.plugin_dir:
            return None
        return os.path.join(
            os.path.dirname(self.plugin_dir), "autosave", "unsaved.mapchar"
        )

    def _autosave(self) -> None:
        """Write the copy when the project has changed since the last one, and
        since its last save. Quiet on failure: a copy that could not be written
        is not worth a dialog every two minutes."""
        path = self._autosave_path()
        if path is None or not self.workspace.entries:
            return
        snapshot = self._snapshot()
        if snapshot == self._autosaved_snapshot or snapshot == self._saved_snapshot:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            save_project(
                path,
                self.workspace.entries,
                self.workspace.current,
                self.workspace.glossary,
            )
        except OSError:
            return
        self._autosaved_snapshot = snapshot

    def _discard_autosave(self, path: str | None = None) -> None:
        path = path or self._autosave_path()
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        self._autosaved_snapshot = None

    def _offer_autosave(self, path: str) -> bool:
        """Before opening ``path``: a newer copy beside it is offered, and taken
        or removed. True when the copy was loaded in the project's place."""
        copy = path + AUTOSAVE_SUFFIX
        if not self._newer_copy(copy, path):
            return False
        if not self._ask(
            "Recover Autosave",
            f"{os.path.basename(path)} has an autosaved copy from "
            f"{self._stamp(copy)}, newer than the file. Open the copy instead?\n\n"
            "'No' deletes the copy.",
        ):
            self._discard_autosave(copy)
            return False
        return True

    def offer_session_recovery(self) -> None:
        """At start: a copy left by a session that had no project file is
        offered back.

        Declined, the copy goes. Recovered, it stays: it is this session's copy
        again — and still the only place the work exists — until the session is
        saved as a project. One that will not load is left where it is, with
        the error, rather than deleted out from under the user.
        """
        if self.project_path or self.workspace.entries:
            return
        path = self._autosave_path()
        if not path or not os.path.exists(path):
            return
        if not self._ask(
            "Recover Session",
            f"A session autosaved at {self._stamp(path)} was never saved as a "
            "project. Recover it?\n\n'No' deletes the copy.",
        ):
            self._discard_autosave(path)
            return
        self._open_recovered(path, None)

    def _open_recovered(self, copy: str, project_path: str | None) -> bool:
        """Open the copy as the project it stands for, and write it again.

        The copy is not discarded by the open: until the recovered project is
        saved it is the newer of the two files, and deleting it would leave a
        crash before the next save with nothing but the older project. It is
        written again from what was just loaded, so it says what is now in
        memory rather than waiting on the timer.
        """
        try:
            load_project(copy)
        except ProjectError as exc:
            self._error(str(exc))
            return False
        opened = self.open_project(copy, recovered_from=project_path, recovered=True)
        if opened:
            self.project_path = project_path
            self._saved_snapshot = "" if project_path else None
            self._update_title()
            self._autosave()
        return opened

    @staticmethod
    def _newer_copy(copy: str, path: str) -> bool:
        try:
            return os.path.getmtime(copy) > os.path.getmtime(path)
        except OSError:
            return False

    @staticmethod
    def _stamp(path: str) -> str:
        try:
            return time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))
            )
        except OSError:
            return "an unknown time"
