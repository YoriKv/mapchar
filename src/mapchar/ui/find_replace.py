"""Find and Replace over the translations of the selected strings, a block, or
the project — for text typed here, or for the glossary's terms.

Matching is code-aware: ``[line]`` in the Find box matches the code and
nothing inside it (:mod:`mapchar.engines.scriptfind`), and a glossary term
never matches inside one (:mod:`mapchar.project.glossary`).

Presentation only: the dialog hands the window what is asked for
(:class:`Search`) and is told which hit the window stands on.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from mapchar.project.glossary import GlossaryTerm
from mapchar.ui.widgets import (
    CompactComboBox,
    ElidedLabel,
    ModeToggle,
    fill_pick,
    fit_chars,
    hint_field,
    select_data,
)
from mapchar.ui.window_layout import remember_layout

SELECTION, BLOCK, PROJECT = "selection", "block", "project"
"""The scopes a search runs over."""


@dataclass(frozen=True)
class Search:
    """What the dialog asks for: text and what replaces it, or the glossary's
    terms and their translations."""

    needle: str = ""
    replacement: str = ""
    case: bool = True
    scope: str = BLOCK
    glossary: bool = False
    """Look for the glossary's terms rather than :attr:`needle`."""
    term: GlossaryTerm | None = None
    """The one term looked for, or every term that has a translation."""
    skip_done: bool = True
    """Leave the strings marked done alone: they have been read through."""


class FindReplaceDialog(QDialog):
    find_next = Signal(object)
    """A :class:`Search` to step to the next hit of."""
    replace_one = Signal(object)
    replace_all = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Find and Replace")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "find_replace")
        self.setModal(False)
        form = QFormLayout(self)
        self.mode = ModeToggle((("Text", False), ("Glossary", True)))
        self.mode.button(False).setToolTip("Find the typed text")
        self.mode.button(True).setToolTip(
            "Replace glossary terms with their translations"
        )
        self.find = hint_field(QLineEdit(), "text, or a [code] matched whole")
        fit_chars(self.find, 28)
        self.replace = QLineEdit()
        self.term = CompactComboBox()
        self.term.setToolTip("Term to find: one, or every translated term")
        self.hit = ElidedLabel("")
        self.case = QCheckBox("Match case")
        self.skip_done = QCheckBox("Skip strings marked done")
        self.skip_done.setChecked(True)
        self.scope = QComboBox()
        self.scope.addItem("Selected strings", SELECTION)
        self.scope.addItem("This block", BLOCK)
        self.scope.addItem("Whole project", PROJECT)
        select_data(self.scope, BLOCK)
        # Held to its own width: a form stretches a field across the row, and
        # a toggle's buttons would drift apart.
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode)
        mode_row.addStretch(1)
        form.addRow("Look for", mode_row)
        form.addRow("Find", self.find)
        form.addRow("Replace with", self.replace)
        form.addRow("Term", self.term)
        form.addRow("Found", self.hit)
        form.addRow("Scope", self.scope)
        form.addRow("", self.case)
        form.addRow("", self.skip_done)
        row = QHBoxLayout()
        b_next = QPushButton("Find Next")
        b_next.setDefault(True)
        b_one = QPushButton("Replace")
        b_all = QPushButton("Replace All")
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.close)
        row.addWidget(b_next)
        row.addWidget(b_one)
        row.addWidget(b_all)
        row.addStretch(1)
        row.addWidget(b_close)
        form.addRow(row)
        self.next_button, self.one_button, self.all_button = b_next, b_one, b_all
        b_next.clicked.connect(lambda: self.find_next.emit(self.search()))
        b_one.clicked.connect(lambda: self.replace_one.emit(self.search()))
        b_all.clicked.connect(lambda: self.replace_all.emit(self.search()))
        self.find.returnPressed.connect(b_next.click)
        self.mode.chosen.connect(lambda _on: self._sync_mode())
        self.set_terms([])
        self._sync_mode()

    # --- what is asked for ---------------------------------------------------

    def search(self) -> Search:
        return Search(
            self.find.text(),
            self.replace.text(),
            self.case.isChecked(),
            self.scope.currentData(),
            self.glossary(),
            self.term.currentData(),
            self.skip_done.isChecked(),
        )

    def glossary(self) -> bool:
        """Whether the glossary's terms are looked for rather than typed text."""
        return bool(self.mode.value())

    def project(self) -> bool:
        """Whether the scope is the whole project."""
        return self.scope.currentData() == PROJECT

    def set_glossary(self, on: bool, term: GlossaryTerm | None = None) -> None:
        """Open on typed text or on the glossary — on one term of it, or all."""
        self.mode.set_value(on)
        if on:
            select_data(self.term, term)
        self._sync_mode()

    def set_scope(self, scope: str) -> None:
        select_data(self.scope, scope)

    def set_terms(self, terms: list[GlossaryTerm]) -> None:
        """The terms there are to look for: the ones with a translation."""
        fill_pick(
            self.term,
            [(f"{t.term} → {t.translation}", t) for t in terms if t.translation],
            none_label="All terms",
        )

    def show_hit(self, text: str) -> None:
        """What the window stands on: the term found and what it becomes."""
        self.hit.setText(text)

    def _sync_mode(self) -> None:
        """Every row stays where it is; the ones the mode has no use for are
        greyed."""
        on = self.glossary()
        for widget in (self.find, self.replace, self.case):
            widget.setEnabled(not on)
        for widget in (self.term, self.hit):
            widget.setEnabled(on)
        if not on:
            self.hit.setText("")


__all__ = ["BLOCK", "PROJECT", "SELECTION", "FindReplaceDialog", "Search"]
