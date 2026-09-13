"""Widgets and widget helpers more than one window needs."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

T = TypeVar("T")


PICKER_WIDTH = 160
"""The closed width of a codec or table picker: one number, so a bar of them
reads as a row whatever names the registry and the project give their items."""


class CompactComboBox(QComboBox):
    """A combo box whose closed button is a stated width in pixels.

    A stock combo reserves the width of its longest item, which long plugin,
    table and preset names turn into dead space in a bar — and a bar whose
    width changes as the items do. Only the size *hints* are set, so a layout
    may still stretch one; the open list is widened back to its longest item,
    so every entry stays readable while choosing.
    """

    def __init__(self, width: int = PICKER_WIDTH, parent: QWidget | None = None):
        super().__init__(parent)
        self._width = width

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().sizeHint()
        hint.setWidth(self._width)
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        hint.setWidth(self._width)
        return hint

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        view = self.view()
        width = view.sizeHintForColumn(0) + view.verticalScrollBar().sizeHint().width()
        view.setMinimumWidth(max(self.width(), width))
        super().showPopup()


class ResultsTable(QTableWidget):
    """A read-only grid of results: whole rows select, and a row is an item.

    The window keeps the list the rows stand for and asks :meth:`pick` which
    of them the selection names.
    """

    def __init__(self, headers: Sequence[str], parent: QWidget | None = None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(list(headers))
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

    def fill(self, rows: Iterable[Sequence[str]]) -> None:
        """Replace every row; columns are sized to what they now hold."""
        rows = list(rows)
        self.setRowCount(len(rows))
        for row, cells in enumerate(rows):
            for col, text in enumerate(cells):
                self.setItem(row, col, QTableWidgetItem(text))
        self.resizeColumnsToContents()

    def current_row(self) -> int | None:
        rows = self.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def pick(self, items: Sequence[T]) -> T | None:
        """The item the selected row stands for, if a row is selected."""
        row = self.current_row()
        return items[row] if row is not None and row < len(items) else None


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


def fill_pick(
    combo: QComboBox,
    items: Iterable[tuple[str, object]],
    none_label: str | None = None,
    keep_current: bool = True,
) -> None:
    """Refill a combo from ``(label, data)`` pairs without emitting signals.

    ``none_label`` heads the list with a ``None`` datum. With ``keep_current``
    the item carrying the data that showed before stays showing, else the
    first item does.
    """
    current = combo.currentData()
    # Put back the state found, not "unblocked": a caller refilling inside its
    # own block (restoring a session) must not have the rest of its work fire.
    was_blocked = combo.blockSignals(True)
    combo.clear()
    if none_label is not None:
        combo.addItem(none_label, None)
    for label, data in items:
        combo.addItem(label, data)
    if keep_current:
        combo.setCurrentIndex(max(combo.findData(current), 0))
    combo.blockSignals(was_blocked)


def select_data(combo: QComboBox, value: object) -> bool:
    """Show the item carrying ``value``; ``False`` when the combo has none."""
    index = combo.findData(value)
    if index < 0:
        return False
    combo.setCurrentIndex(index)
    return True


__all__ = [
    "PICKER_WIDTH",
    "CancellableRun",
    "CompactComboBox",
    "ModalProgress",
    "ResultsTable",
    "fill_pick",
    "select_data",
]
