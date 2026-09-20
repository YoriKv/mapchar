"""What every window beside the main one opens with, and the shape two share.

A tool window's first three lines look trivial and are not: the default size
has to be set *before* the stored geometry is restored, or the restore is
undone a moment later and the size a user chose is thrown away every launch.
:class:`ToolWindow` does those lines once so no window can get the order wrong.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from mapchar.ui.progress import CancellableRun
from mapchar.ui.widgets import ElidedLabel, EscapeCloses, ResultsTable
from mapchar.ui.window_layout import remember_layout


class ToolWindow(EscapeCloses, QWidget):
    """A window beside the main one: Esc closes it, and its size and position
    are remembered between runs (:mod:`mapchar.ui.window_layout`).

    ``size`` is what a machine with nothing stored opens at, which is why it is
    set before the restore rather than at the end of the subclass's
    ``__init__``: a resize after the restore is the stored size overwritten.

    ``QWidget`` is among this class's bases, so a mixin that overrides one of
    its virtuals (:class:`~mapchar.ui.icon_font.ThemedIcons` and its
    ``changeEvent``) goes *before* ``ToolWindow`` in a subclass's bases, or
    ``QWidget``'s own method is found first and the mixin's never runs.
    """

    def __init__(
        self,
        title: str,
        key: str,
        size: tuple[int, int],
        parent: QWidget | None = None,
        flags: Qt.WindowType = Qt.WindowType.Window,
    ) -> None:
        super().__init__(parent, flags)
        self.setWindowTitle(title)
        self.resize(*size)
        self._layout = remember_layout(self, key)


class ResultsRunWindow(ToolWindow, CancellableRun):
    """A tool window that runs one long search over the file and lists its hits.

    Top to bottom: a parameter row ending in Run and Stop, the progress line
    those two drive, the results, and one action button over the row picked.
    Selecting a row shows what it names in the raw view and arms the button.

    A subclass fills the parameter row (:meth:`_parameters`), does the work
    (:meth:`_run`), spells one result as cells (:meth:`_row`), says which bytes
    it stands for (:meth:`_span`) and what the action button does with it
    (:meth:`_act`).
    """

    go_to = Signal(int, int)
    """Offset and length to select in the raw view."""

    def __init__(
        self,
        title: str,
        key: str,
        size: tuple[int, int],
        *,
        run_label: str,
        verb: str,
        headers: Sequence[str],
        action_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, key, size, parent)
        self._items: Sequence[object] = ()
        """What each row stands for, in the order the rows are in."""
        box = QVBoxLayout(self)
        row = QHBoxLayout()
        self.run = QPushButton(run_label)
        self.stop = QPushButton("Stop")
        self._parameters(row)
        row.addWidget(self.run)
        row.addWidget(self.stop)
        box.addLayout(row)
        self.status = ElidedLabel("")
        box.addWidget(self.status)
        self.results = ResultsTable(headers)
        box.addWidget(self.results, 1)
        bottom = QHBoxLayout()
        self.action = QPushButton(action_label)
        self.action.setEnabled(False)
        bottom.addStretch(1)
        bottom.addWidget(self.action)
        box.addLayout(bottom)
        self.bind_run(self.run, self.stop, self.status, verb)
        self.run.clicked.connect(self._run)
        self.results.itemSelectionChanged.connect(self._on_select)
        self.action.clicked.connect(self._act)

    def fill_results(self, items: Sequence[object]) -> None:
        """Show these results, each row remembering the one it stands for."""
        self._items = items
        self.results.fill(self._row(item) for item in items)

    def selected(self) -> object | None:
        """The result the selected row stands for, if a row is selected."""
        return self.results.pick(self._items)

    def _on_select(self) -> None:
        self.action.setEnabled(self.selected() is not None)
        self._jump()

    def _jump(self) -> None:
        item = self.selected()
        if item is not None:
            self.go_to.emit(*self._span(item))

    # -- what a subclass supplies ---------------------------------------------

    def _parameters(self, row: QHBoxLayout) -> None:
        """Fill the parameter row; Run and Stop follow whatever it adds."""
        raise NotImplementedError

    def _run(self) -> None:
        """Do the work, and hand what it found to :meth:`fill_results`."""
        raise NotImplementedError

    def _row(self, item: object) -> Sequence[str]:
        """One result as the cells of its row."""
        raise NotImplementedError

    def _span(self, item: object) -> tuple[int, int]:
        """The offset and length a result stands for."""
        raise NotImplementedError

    def _act(self) -> None:
        """What the action button does with the selected result."""
        raise NotImplementedError


__all__ = ["ResultsRunWindow", "ToolWindow"]
