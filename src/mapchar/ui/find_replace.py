"""Find and Replace over the translations of a block."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)


class FindReplaceDialog(QDialog):
    find_next = Signal(str, bool)
    replace_one = Signal(str, str, bool)
    replace_all = Signal(str, str, bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Find and Replace")
        self.setModal(False)
        form = QFormLayout(self)
        self.find = QLineEdit()
        self.replace = QLineEdit()
        self.case = QCheckBox("Match case")
        form.addRow("Find", self.find)
        form.addRow("Replace with", self.replace)
        form.addRow("", self.case)
        row = QHBoxLayout()
        b_next = QPushButton("Find next")
        b_one = QPushButton("Replace")
        b_all = QPushButton("Replace all")
        row.addWidget(b_next)
        row.addWidget(b_one)
        row.addWidget(b_all)
        form.addRow(row)
        b_next.clicked.connect(
            lambda: self.find_next.emit(self.find.text(), self.case.isChecked())
        )
        b_one.clicked.connect(
            lambda: self.replace_one.emit(
                self.find.text(), self.replace.text(), self.case.isChecked()
            )
        )
        b_all.clicked.connect(
            lambda: self.replace_all.emit(
                self.find.text(), self.replace.text(), self.case.isChecked()
            )
        )
        self.find.returnPressed.connect(b_next.click)
