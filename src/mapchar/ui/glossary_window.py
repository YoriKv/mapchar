"""The Glossary window: the project's terms, and the ones in the string on
screen.

Presentation only: the window hands it the terms and the selected string's
original, and takes back the list as edited and the text to type into the
translation.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.text import fold
from mapchar.project.glossary import GlossaryTerm, matching_terms
from mapchar.ui.widgets import (
    EscapeCloses,
    ResultsTable,
    hint_field,
    show_elided_tooltips,
)
from mapchar.ui.window_layout import remember_layout

HEADERS = ["Term", "Translation", "Notes"]


class GlossaryWindow(EscapeCloses, QWidget):
    changed = Signal(list)
    """The terms as edited here: a list of :class:`GlossaryTerm`."""
    insert_requested = Signal(str)
    """Type this into the translation being edited."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Glossary")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "glossary_window")
        self._terms: list[GlossaryTerm] = []
        self._context = ""
        self._hits: list[GlossaryTerm] = []
        self._filling = False

        # The terms the string on screen holds, and a way to type one in.
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.hits_label = QLabel("In this string")
        self.hits = ResultsTable(HEADERS)
        self.insert = QPushButton("Insert Translation")
        self.insert.setToolTip(
            "Type the term's translation into the translation being edited; "
            "double-clicking a row does the same"
        )
        self.insert.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.hits_label, 1)
        row.addWidget(self.insert)
        tl.addLayout(row)
        tl.addWidget(self.hits, 1)

        # Every term, editable in place.
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.filter = hint_field(
            QLineEdit(),
            "filter terms",
            "Words in any order over term, translation and notes",
        )
        self.filter.setClearButtonEnabled(True)
        self.add = QPushButton("Add")
        self.remove = QPushButton("Remove")
        self.remove.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(QLabel("All terms"))
        row.addWidget(self.filter, 1)
        row.addWidget(self.add)
        row.addWidget(self.remove)
        bl.addLayout(row)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        show_elided_tooltips(self.table)
        bl.addWidget(self.table, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self.hits.itemSelectionChanged.connect(
            lambda: self.insert.setEnabled(self.hits.current_row() is not None)
        )
        self.hits.itemActivated.connect(lambda _item: self._insert())
        self.insert.clicked.connect(self._insert)
        self.filter.textChanged.connect(self._apply_filter)
        self.add.clicked.connect(self._add)
        self.remove.clicked.connect(self._remove)
        self.table.itemChanged.connect(self._on_edited)
        self.table.itemSelectionChanged.connect(
            lambda: self.remove.setEnabled(bool(self.table.selectedItems()))
        )
        self.resize(560, 480)

    # --- what is shown ------------------------------------------------------

    def set_terms(self, terms: list[GlossaryTerm]) -> None:
        self._terms = list(terms)
        self._filling = True
        was = self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(terms))
            for r, term in enumerate(terms):
                for c, text in enumerate((term.term, term.translation, term.notes)):
                    self.table.setItem(r, c, QTableWidgetItem(text))
        finally:
            self.table.blockSignals(was)
            self._filling = False
        self.table.resizeColumnToContents(0)
        self._apply_filter()
        self._refresh_hits()

    def set_context(self, text: str) -> None:
        """The original of the string on screen: what the hits are found in."""
        if text == self._context:
            return
        self._context = text
        self._refresh_hits()

    def _refresh_hits(self) -> None:
        self._hits = matching_terms(self._terms, self._context)
        self.hits.fill((t.term, t.translation, t.notes) for t in self._hits)
        n = len(self._hits)
        self.hits_label.setText(
            f"In this string: {n} term(s)" if n else "In this string"
        )
        self.insert.setEnabled(False)

    def _apply_filter(self) -> None:
        words = fold(self.filter.text()).split()
        for r in range(self.table.rowCount()):
            hay = fold(
                " ".join(
                    self.table.item(r, c).text() if self.table.item(r, c) else ""
                    for c in range(len(HEADERS))
                )
            )
            self.table.setRowHidden(r, bool(words) and not all(w in hay for w in words))

    # --- edits ----------------------------------------------------------------

    def _collect(self) -> list[GlossaryTerm]:
        """The table as terms; a row with nothing in it is not one."""
        out = []
        for r in range(self.table.rowCount()):
            cells = [
                self.table.item(r, c).text() if self.table.item(r, c) else ""
                for c in range(len(HEADERS))
            ]
            if any(cell.strip() for cell in cells):
                out.append(GlossaryTerm(cells[0].strip(), cells[1], cells[2]))
        return out

    def _on_edited(self, _item: QTableWidgetItem | None) -> None:
        if self._filling:
            return
        terms = self._collect()
        if terms != self._terms:
            self._terms = terms
            self._refresh_hits()
            self.changed.emit(terms)

    def _add(self) -> None:
        """A new row, opened on its term; it becomes a term once typed in."""
        r = self.table.rowCount()
        self._filling = True
        try:
            self.table.insertRow(r)
            for c in range(len(HEADERS)):
                self.table.setItem(r, c, QTableWidgetItem(""))
        finally:
            self._filling = False
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        if not rows:
            return
        self._filling = True
        try:
            for r in rows:
                self.table.removeRow(r)
        finally:
            self._filling = False
        self._on_edited(None)

    def _insert(self) -> None:
        term = self.hits.pick(self._hits)
        if term is not None:
            self.insert_requested.emit(term.translation or term.term)


__all__ = ["GlossaryWindow"]
