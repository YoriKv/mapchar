"""How long work says how far it has got, and how it is stopped.

Both of these hand the engines the ``progress(done, total) -> bool`` callback
they take, and both pump the event loop so the button that stops the run can be
clicked at all; they differ in where that button is. A tool window with a
run/stop row of its own uses :class:`CancellableRun`, and work started from a
menu, which has nowhere to put one, uses :class:`ModalProgress`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PySide6.QtWidgets import QLabel, QPushButton, QWidget


class CancellableRun:
    """A run button, a stop button and a progress line over one long search.

    A window builds the three widgets, hands them to :meth:`bind_run`, wraps
    the work in :meth:`running` and passes :meth:`progress` to the engine as
    its progress callback; :attr:`cancelled` says whether Stop was pressed.
    """

    def bind_run(
        self, run: QPushButton, stop: QPushButton, status: QLabel, verb: str
    ) -> None:
        self._run_button = run
        self._stop_button = stop
        self._status_label = status
        self._verb = verb
        self._cancelled = False
        stop.setEnabled(False)
        stop.clicked.connect(self.cancel)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    @contextmanager
    def running(self) -> Iterator[None]:
        self._cancelled = False
        self._run_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        try:
            yield
        finally:
            self._run_button.setEnabled(True)
            self._stop_button.setEnabled(False)

    @contextmanager
    def running_as(self, run: QPushButton, verb: str) -> Iterator[None]:
        """One run started from another button, on the same Stop and progress
        line: a window with two long actions has one way out of either."""
        was_run, was_verb = self._run_button, self._verb
        self._run_button, self._verb = run, verb
        try:
            with self.running():
                yield
        finally:
            self._run_button, self._verb = was_run, was_verb

    def progress(self, done: int, total: int) -> bool:
        """Report how far the work is; ``False`` asks the engine to stop."""
        self._status_label.setText(f"{self._verb}… {done * 100 // max(total, 1)}%")
        QApplication.processEvents()
        return not self._cancelled


class ModalProgress:
    """A progress bar with a Stop button over one long call, in front of a window.

    :class:`CancellableRun`'s counterpart for work started from a **menu** rather
    than from a tool window that has a run/stop row of its own: the search for
    pointers has nowhere to put those two buttons, and a menu row that freezes the
    window for a minute with no way out is the one thing every long operation here
    is supposed not to do.

    Used as a context manager; :meth:`progress` is what the engine is handed, and
    it pumps the event loop so the Stop button can be clicked at all. The engine
    is asked to stop rather than interrupted, so a cancelled run still returns
    whatever it had found by then.
    """

    def __init__(self, parent: QWidget | None, title: str, label: str) -> None:
        dialog = QProgressDialog(label, "Stop", 0, 1, parent)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)  # the work has already started
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        self.dialog = dialog

    def __enter__(self) -> ModalProgress:
        self.dialog.setValue(0)
        return self

    def __exit__(self, *exc: object) -> None:
        self.dialog.reset()
        self.dialog.close()
        self.dialog.deleteLater()

    @property
    def cancelled(self) -> bool:
        return self.dialog.wasCanceled()

    def cancel(self) -> None:
        self.dialog.cancel()

    def progress(self, done: int, total: int) -> bool:
        """Report how far the work is; ``False`` asks the engine to stop."""
        self.dialog.setMaximum(max(total, 1))
        self.dialog.setValue(min(done, max(total, 1)))
        QApplication.processEvents()
        return not self.dialog.wasCanceled()


__all__ = ["CancellableRun", "ModalProgress"]
