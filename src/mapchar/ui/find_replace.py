"""Find and Replace over the translations of a block, or of the project.

Matching is code-aware: ``[line]`` in the Find box matches the code and
nothing inside it (:mod:`mapchar.engines.scriptfind`).
"""

from __future__ import annotations

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

from mapchar.ui.widgets import fit_chars, hint_field
from mapchar.ui.window_layout import remember_layout


class FindReplaceDialog(QDialog):
    find_next = Signal(str, bool, bool)
    """Needle, match case, whole project (else this block)."""
    replace_one = Signal(str, str, bool, bool)
    replace_all = Signal(str, str, bool, bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Find and Replace")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "find_replace")
        self.setModal(False)
        form = QFormLayout(self)
        self.find = hint_field(QLineEdit(), "text, or a [code] matched whole")
        fit_chars(self.find, 28)
        self.replace = QLineEdit()
        self.case = QCheckBox("Match case")
        self.scope = QComboBox()
        self.scope.addItem("This block", False)
        self.scope.addItem("Whole project", True)
        form.addRow("Find", self.find)
        form.addRow("Replace with", self.replace)
        form.addRow("Scope", self.scope)
        form.addRow("", self.case)
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
        b_next.clicked.connect(
            lambda: self.find_next.emit(
                self.find.text(), self.case.isChecked(), self.project()
            )
        )
        b_one.clicked.connect(
            lambda: self.replace_one.emit(
                self.find.text(),
                self.replace.text(),
                self.case.isChecked(),
                self.project(),
            )
        )
        b_all.clicked.connect(
            lambda: self.replace_all.emit(
                self.find.text(),
                self.replace.text(),
                self.case.isChecked(),
                self.project(),
            )
        )
        self.find.returnPressed.connect(b_next.click)

    def project(self) -> bool:
        """Whether the scope is the whole project rather than this block."""
        return bool(self.scope.currentData())
