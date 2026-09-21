"""The Project Strings window: every string of every block in one list, to
filter across the project and jump from.

Presentation only: the window hands it the strings and takes back which one
to go to.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.textmatch import matches_words, words_of
from mapchar.ui.strings_view import STATUS_FILTERS, status_matches
from mapchar.ui.tool_window import ToolWindow
from mapchar.ui.widgets import ElidedLabel, ResultsTable, hint_field


@dataclass(frozen=True)
class ProjectString:
    """One string of one block, as the list shows it."""

    entry: object
    """The block entry."""
    block: str
    index: int
    original: str
    translation: str
    status: str
    notes: str
    misses: str = ""
    """The glossary terms the translation has some other way
    (:attr:`~mapchar.ui.strings_view.RowData.misses`)."""


class ProjectStringsWindow(ToolWindow):
    go_to = Signal(object, int)
    """The block entry and the index of the string to open."""
    refresh_requested = Signal()
    """Read every block again: on show, and on the Refresh button."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            "Project Strings", "project_strings_window", (900, 480), parent
        )
        self._all: list[ProjectString] = []
        self._shown: list[ProjectString] = []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.filter = hint_field(
            QLineEdit(),
            "words in any order",
            "Matches block name, original, translation and notes",
        )
        self.filter.setClearButtonEnabled(True)
        self.status_filter = QComboBox()
        self.status_filter.addItems(STATUS_FILTERS)
        self.refresh = QPushButton("Refresh")
        self.refresh.setToolTip("Re-read every block's strings")
        row.addWidget(self.filter, 1)
        row.addWidget(self.status_filter)
        row.addWidget(self.refresh)
        layout.addLayout(row)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)
        self.results = ResultsTable(
            ["Block", "#", "Original", "Translation", "Status", "Notes"]
        )
        layout.addWidget(self.results, 1)
        self.filter.textChanged.connect(self._fill)
        self.status_filter.currentIndexChanged.connect(self._fill)
        self.refresh.clicked.connect(self.refresh_requested)
        self.results.itemActivated.connect(lambda _item: self._jump())

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.refresh_requested.emit()

    def set_strings(self, strings: list[ProjectString]) -> None:
        self._all = list(strings)
        self._fill()

    def _fill(self) -> None:
        words = words_of(self.filter.text())
        status = self.status_filter.currentText()
        shown = []
        for s in self._all:
            if not status_matches(status, s.status, s.misses):
                continue
            if not matches_words(words, s.block, s.original, s.translation, s.notes):
                continue
            shown.append(s)
        self._shown = shown
        self.results.fill(
            (
                s.block,
                str(s.index),
                s.original.replace("\n", "↵"),
                s.translation.replace("\n", "↵"),
                s.status,
                s.notes,
            )
            for s in shown
        )
        blocks = len({s.block for s in self._all})
        self.status.setText(
            f"{len(shown)} of {len(self._all)} string(s) in {blocks} block(s)"
        )

    def _jump(self) -> None:
        s = self.results.pick(self._shown)
        if s is not None:
            self.go_to.emit(s.entry, s.index)


__all__ = ["ProjectString", "ProjectStringsWindow"]
