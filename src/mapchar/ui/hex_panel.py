"""The Hex panel: a dump of the decoded buffer, with a line to overtype bytes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

WINDOW = 4096
ROW = 16


class HexPanel(QWidget):
    go_to_requested = Signal(int)
    overtype_requested = Signal(int, bytes)
    find_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._data: bytes = b""
        self._offset = 0
        self._selection: tuple[int, int] | None = None
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        top = QHBoxLayout()
        self.goto = QLineEdit()
        self.goto.setPlaceholderText("go to (hex)")
        self.goto.setMaximumWidth(110)
        self.find = QLineEdit()
        self.find.setPlaceholderText('find: hex bytes or "text"')
        self.follow = QCheckBox("Follow selection")
        self.follow.setChecked(True)
        top.addWidget(QLabel("Go to"))
        top.addWidget(self.goto)
        top.addWidget(self.find, 1)
        top.addWidget(self.follow)
        bottom = QHBoxLayout()
        self.at_label = QLabel("At")
        self.at = QLineEdit()
        self.at.setMaximumWidth(110)
        self.bytes = QLineEdit()
        self.bytes.setPlaceholderText(
            "hex bytes to write at that offset, Enter to apply"
        )
        self.apply = QPushButton("Overtype")
        bottom.addWidget(self.at_label)
        bottom.addWidget(self.at)
        bottom.addWidget(self.bytes, 1)
        bottom.addWidget(self.apply)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addLayout(bottom)
        self.goto.returnPressed.connect(self._on_goto)
        self.find.returnPressed.connect(
            lambda: self.find_requested.emit(self.find.text())
        )
        self.bytes.returnPressed.connect(self._on_apply)
        self.apply.clicked.connect(self._on_apply)

    def set_data(
        self, data: bytes, offset: int, selection: tuple[int, int] | None
    ) -> None:
        self._data = data
        self._selection = selection
        if selection and self.follow.isChecked():
            offset = max(0, selection[0] - selection[0] % ROW)
        self._offset = offset - offset % ROW
        if not self.isVisible():
            return
        self._render()

    def refresh(self) -> None:
        self._render()

    def _render(self) -> None:
        lines = []
        end = min(self._offset + WINDOW, len(self._data))
        for at in range(self._offset, end, ROW):
            chunk = self._data[at : at + ROW]
            hexes = " ".join(f"{b:02X}" for b in chunk)
            ascii_ = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
            lines.append(f"{at:06X}  {hexes:<{ROW * 3 - 1}}  {ascii_}")
        self.view.setPlainText("\n".join(lines))
        if self._selection:
            s, e = self._selection
            if self._offset <= s < end:
                row = (s - self._offset) // ROW
                col = (s - self._offset) % ROW
                line_len = 6 + 2 + ROW * 3 - 1 + 2 + ROW + 1
                start = row * line_len + 8 + col * 3
                n = min(e, end) - s
                rows_span = (col + n - 1) // ROW
                length = n * 3 - 1 + rows_span * (line_len - ROW * 3)
                cursor = self.view.textCursor()
                cursor.setPosition(start)
                cursor.setPosition(
                    start + max(length, 2), QTextCursor.MoveMode.KeepAnchor
                )
                self.view.setTextCursor(cursor)
            self.at.setText(f"{s:X}")

    def _on_goto(self) -> None:
        try:
            self.go_to_requested.emit(int(self.goto.text().replace("$", ""), 16))
        except ValueError:
            pass

    def _on_apply(self) -> None:
        try:
            at = int(self.at.text().replace("$", ""), 16)
            data = bytes.fromhex(self.bytes.text().replace("$", "").replace(",", " "))
        except ValueError:
            self.bytes.setStyleSheet("")
            return
        if data:
            self.overtype_requested.emit(at, data)


__all__ = ["HexPanel", "Qt"]
