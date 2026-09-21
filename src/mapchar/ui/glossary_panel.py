"""The Glossary panel: the project's terms, and the ones in the string on
screen.

Presentation only: the window hands it the terms, the selected string's
original, how often each term is used and which translations the block's table
cannot spell, and takes back the list as edited, the text to type into the
translation, and what is asked of a term — replacing it through the strings,
listing the strings that hold it.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.textmatch import matches_words, words_of
from mapchar.project.glossary import GlossaryTerm, matching_terms
from mapchar.ui import theme
from mapchar.ui.bars import FlowLayout
from mapchar.ui.widgets import ResultsTable, hint_field, show_elided_tooltips

COL_TERM, COL_TRANSLATION, COL_MATCH, COL_USES, COL_NOTES = range(5)
HEADERS = ["Term", "Translation", "Match", "Uses", "Notes"]
TEXT_COLUMNS = (COL_TERM, COL_TRANSLATION, COL_NOTES)
"""The cells typed into, which are also what the filter reads."""
_LOCKED = QTableWidgetItem().flags() & ~Qt.ItemFlag.ItemIsEditable


def match_mark(term: GlossaryTerm) -> str:
    """How a term's matching options read in the Match column."""
    return " ".join(
        mark for mark, on in (("Aa", term.match_case), ("W", term.whole_word)) if on
    )


class GlossaryPanel(QWidget):
    changed = Signal(list)
    """The terms as edited here: a list of :class:`GlossaryTerm`."""
    insert_requested = Signal(str)
    """Type this into the translation being edited."""
    replace_requested = Signal(object)
    """Step through the strings replacing this term — or, ``None``, every term."""
    strings_requested = Signal(object)
    """List the project's strings that hold this term."""
    uses_requested = Signal()
    """Count how many strings of the project hold each term."""
    import_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._terms: list[GlossaryTerm] = []
        self._context = ""
        self._hits: list[GlossaryTerm] = []
        self._uses: dict[GlossaryTerm, int] = {}
        self._unspelled: dict[GlossaryTerm, str] = {}
        self._filling = False

        # The terms the string on screen holds, and a way to type one in.
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.hits_label = QLabel("In this string")
        self.hits = ResultsTable(HEADERS[:2])
        self.insert = QPushButton("Insert")
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
        row.addWidget(self.filter, 1)
        row.addWidget(self.add)
        row.addWidget(self.remove)
        bl.addLayout(row)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # A dock is narrow: the text columns share its width and the two
        # marks take what they hold, so nothing is scrolled sideways for.
        columns = self.table.horizontalHeader()
        for col in range(len(HEADERS)):
            columns.setSectionResizeMode(
                col,
                QHeaderView.ResizeMode.Stretch
                if col in TEXT_COLUMNS
                else QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        header = self.table.horizontalHeaderItem(COL_MATCH)
        header.setToolTip("Aa: only in the term's own case · W: only as a whole word")
        header = self.table.horizontalHeaderItem(COL_USES)
        header.setToolTip("How many of the project's strings hold the term")
        show_elided_tooltips(self.table)
        bl.addWidget(self.table, 1)
        self.replace_all = QPushButton("Replace…")
        self.replace_all.setToolTip(
            "Step through the strings, putting each term's translation in its place"
        )
        self.count = QPushButton("Count Uses")
        self.count.setToolTip(
            "Read every block and count the strings holding each term"
        )
        self.import_button = QPushButton("Import…")
        self.import_button.setToolTip("Lay a TSV or CSV of terms over these")
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Write the terms to a TSV or CSV")
        # Wrapping rather than setting the dock's minimum width.
        buttons = QWidget()
        flow = FlowLayout(buttons)
        flow.setContentsMargins(0, 0, 0, 0)
        for button in (
            self.replace_all,
            self.count,
            self.import_button,
            self.export_button,
        ):
            flow.addWidget(button)
        bl.addWidget(buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)

        self.hits.itemSelectionChanged.connect(
            lambda: self.insert.setEnabled(self.hits.current_row() is not None)
        )
        self.hits.itemActivated.connect(lambda _item: self._insert())
        self.insert.clicked.connect(self._insert)
        self.filter.textChanged.connect(self._apply_filter)
        self.add.clicked.connect(lambda: self.add_term())
        self.remove.clicked.connect(self._remove)
        self.table.itemChanged.connect(self._on_edited)
        self.table.itemSelectionChanged.connect(
            lambda: self.remove.setEnabled(bool(self.table.selectedItems()))
        )
        self.table.customContextMenuRequested.connect(self._on_menu)
        self.replace_all.clicked.connect(lambda: self.replace_requested.emit(None))
        self.count.clicked.connect(self.uses_requested)
        self.import_button.clicked.connect(self.import_requested)
        self.export_button.clicked.connect(self.export_requested)

    # --- what is shown ------------------------------------------------------

    def terms(self) -> list[GlossaryTerm]:
        return list(self._terms)

    def set_terms(self, terms: list[GlossaryTerm]) -> None:
        """The project's terms. The ones the grid already shows — an edit made
        here, coming back from the window — leave it as it stands."""
        if terms == self._terms and self._rows_are(terms):
            return
        self._terms = list(terms)
        self._rebuild()

    def _rows_are(self, terms: list[GlossaryTerm]) -> bool:
        return [self._term_at(r) for r in range(self.table.rowCount())] == terms

    def _rebuild(self) -> None:
        terms = self._terms
        self._filling = True
        was = self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(terms))
            for r, term in enumerate(terms):
                self._fill_row(r, term)
        finally:
            self.table.blockSignals(was)
            self._filling = False
        self._apply_filter()
        self._refresh_hits()

    def _fill_row(self, r: int, term: GlossaryTerm) -> None:
        cells = {
            COL_TERM: term.term,
            COL_TRANSLATION: term.translation,
            COL_MATCH: match_mark(term),
            COL_USES: str(self._uses[term]) if term in self._uses else "",
            COL_NOTES: term.notes,
        }
        for col, text in cells.items():
            item = QTableWidgetItem(text)
            if col not in TEXT_COLUMNS:
                item.setFlags(_LOCKED)
            self.table.setItem(r, col, item)
        self.table.item(r, COL_TERM).setData(Qt.ItemDataRole.UserRole, term)
        why = self._unspelled.get(term)
        if why:
            cell = self.table.item(r, COL_TRANSLATION)
            cell.setForeground(theme.ERROR_INK)
            cell.setToolTip(f"The block's table cannot spell this: {why}")

    def set_context(self, text: str) -> None:
        """The original of the string on screen: what the hits are found in."""
        if text == self._context:
            return
        self._context = text
        self._refresh_hits()

    def set_uses(self, uses: dict[GlossaryTerm, int]) -> None:
        """How many of the project's strings hold each term, as last counted."""
        self._uses = dict(uses)
        self._rebuild()

    def set_unspelled(self, unspelled: dict[GlossaryTerm, str]) -> None:
        """The terms whose translation the block on screen cannot encode, each
        with why: marked before a replace runs into it string by string."""
        if unspelled == self._unspelled:
            return
        self._unspelled = dict(unspelled)
        self._rebuild()

    def _refresh_hits(self) -> None:
        self._hits = matching_terms(self._terms, self._context)
        self.hits.fill((t.term, t.translation) for t in self._hits)
        n = len(self._hits)
        self.hits_label.setText(
            f"In this string: {n} term(s)" if n else "In this string"
        )
        self.insert.setEnabled(False)

    def _cells(self, r: int) -> list[str]:
        """One row's typed cells as their text, one nobody has typed in as ""."""
        return [
            self.table.item(r, c).text() if self.table.item(r, c) else ""
            for c in TEXT_COLUMNS
        ]

    def _term_at(self, r: int) -> GlossaryTerm | None:
        """The term row ``r`` was filled from — before whatever was typed since."""
        item = self.table.item(r, COL_TERM)
        held = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return held if isinstance(held, GlossaryTerm) else None

    def _apply_filter(self) -> None:
        words = words_of(self.filter.text())
        for r in range(self.table.rowCount()):
            self.table.setRowHidden(r, not matches_words(words, *self._cells(r)))

    # --- edits ----------------------------------------------------------------

    def _collect(self) -> list[GlossaryTerm]:
        """The table as terms, each keeping the options its row was filled
        with; a row with nothing in it is not one."""
        out = []
        for r in range(self.table.rowCount()):
            term, translation, notes = self._cells(r)
            if not any(cell.strip() for cell in (term, translation, notes)):
                continue
            was = self._term_at(r) or GlossaryTerm("")
            out.append(
                replace(was, term=term.strip(), translation=translation, notes=notes)
            )
        return out

    def _on_edited(self, _item: QTableWidgetItem | None) -> None:
        if self._filling:
            return
        self._commit(self._collect(), rebuild=False)

    def _commit(self, terms: list[GlossaryTerm], rebuild: bool = True) -> None:
        """The terms as they now are, out to the window.

        A cell typed into leaves the grid as it stands — a rebuild would take
        the cursor out from under a Tab to the next cell — and only has each
        row remember the term it now is.
        """
        if terms == self._terms:
            return
        if rebuild:
            self._terms = list(terms)
            self._rebuild()
        else:
            self._terms = list(terms)
            self._remember_rows(terms)
            self._refresh_hits()
        self.changed.emit(terms)

    def _remember_rows(self, terms: list[GlossaryTerm]) -> None:
        """Hand each row that holds a term the term it now is."""
        held = iter(terms)
        self._filling = True
        try:
            for r in range(self.table.rowCount()):
                if not any(cell.strip() for cell in self._cells(r)):
                    continue
                item = self.table.item(r, COL_TERM)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, next(held, None))
        finally:
            self._filling = False

    def add_term(self, term: str = "", translation: str = "") -> None:
        """A new row, opened on the first cell still to be typed; it becomes a
        term once something is in it."""
        self.filter.clear()
        r = self.table.rowCount()
        self._filling = True
        try:
            self.table.insertRow(r)
            self._fill_row(r, GlossaryTerm(term, translation))
        finally:
            self._filling = False
        if term:
            self._commit(self._collect(), rebuild=False)
        col = COL_TRANSLATION if term else COL_TERM
        self.table.setCurrentCell(r, col)
        self.table.editItem(self.table.item(r, col))

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedItems()})

    def _remove(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        self._filling = True
        try:
            for r in reversed(rows):
                self.table.removeRow(r)
        finally:
            self._filling = False
        self._on_edited(None)

    def _toggle(self, rows: list[int], option: str, on: bool) -> None:
        """One matching option, on every selected term."""
        terms = self._collect()
        if len(terms) != self.table.rowCount():
            return  # a blank row is being typed into: nothing to map rows onto
        for r in rows:
            terms[r] = replace(terms[r], **{option: on})
        self._commit(terms)

    def _on_menu(self, pos: QPoint) -> None:
        rows = self._selected_rows()
        term = self._term_at(rows[0]) if rows else None
        if term is None:
            return
        menu = QMenu(self)
        menu.addAction(
            "&Replace in Strings…", lambda: self.replace_requested.emit(term)
        ).setEnabled(bool(term.translation))
        menu.addAction(
            "Show in &Project Strings", lambda: self.strings_requested.emit(term)
        )
        menu.addSeparator()
        for text, option in (
            ("Match &Case", "match_case"),
            ("Whole &Word", "whole_word"),
        ):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(getattr(term, option))
            action.triggered.connect(
                lambda on, option=option: self._toggle(rows, option, on)
            )
        menu.addSeparator()
        menu.addAction("Re&move", self._remove)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _insert(self) -> None:
        term = self.hits.pick(self._hits)
        if term is not None:
            self.insert_requested.emit(term.translation or term.term)


__all__ = ["GlossaryPanel", "match_mark"]
