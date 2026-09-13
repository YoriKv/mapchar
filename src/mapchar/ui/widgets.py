"""Widgets and widget helpers more than one window needs."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

T = TypeVar("T")


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
    combo.blockSignals(True)
    combo.clear()
    if none_label is not None:
        combo.addItem(none_label, None)
    for label, data in items:
        combo.addItem(label, data)
    if keep_current:
        combo.setCurrentIndex(max(combo.findData(current), 0))
    combo.blockSignals(False)


def select_data(combo: QComboBox, value: object) -> bool:
    """Show the item carrying ``value``; ``False`` when the combo has none."""
    index = combo.findData(value)
    if index < 0:
        return False
    combo.setCurrentIndex(index)
    return True


__all__ = ["CancellableRun", "ResultsTable", "fill_pick", "select_data"]
