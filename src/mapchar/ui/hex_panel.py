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


LINE_LEN = 6 + 2 + ROW * 3 - 1 + 2 + ROW + 1
"""Characters per dump line, newline included."""


class _HexView(QPlainTextEdit):
    """The dump: typing a hex digit over a byte overtypes that nibble in place."""

    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._pending: tuple[int, int] | None = None
        """``(byte offset, high nibble)`` after the first of two digits."""

    def _byte_at_cursor(self) -> tuple[int, int] | None:
        """``(absolute byte offset, nibble index)`` under the caret, if on hex."""
        pos = self.textCursor().position()
        row, col = divmod(pos, LINE_LEN)
        hex_start = 8
        if col < hex_start or col >= hex_start + ROW * 3 - 1:
            return None
        rel = col - hex_start
        if rel % 3 == 2:
            return None
        byte = self._panel._offset + row * ROW + rel // 3
        if byte >= len(self._panel._data):
            return None
        return byte, rel % 3

    def keyPressEvent(self, event) -> None:
        text = event.text().upper()
        if len(text) == 1 and text in "0123456789ABCDEF":
            where = self._byte_at_cursor()
            if where is not None:
                offset, nibble = where
                current = self._panel._data[offset]
                digit = int(text, 16)
                if nibble == 0:
                    new = (digit << 4) | (current & 0x0F)
                else:
                    new = (current & 0xF0) | digit
                cursor = self.textCursor()
                at = cursor.position()
                self._panel.overtype_requested.emit(offset, bytes([new]))
                # Move to the next nibble (skipping the separating space).
                step = 1 if nibble == 0 else 2
                cursor.setPosition(min(at + step, len(self.toPlainText())))
                self.setTextCursor(cursor)
                return
        super().keyPressEvent(event)


class HexPanel(QWidget):
    go_to_requested = Signal(int)
    overtype_requested = Signal(int, bytes)
    find_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._data: bytes = b""
        self._offset = 0
        self._selection: tuple[int, int] | None = None
        self.view = _HexView(self)
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
        caret = self.view.textCursor().position()
        lines = []
        end = min(self._offset + WINDOW, len(self._data))
        for at in range(self._offset, end, ROW):
            chunk = self._data[at : at + ROW]
            hexes = " ".join(f"{b:02X}" for b in chunk)
            ascii_ = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
            lines.append(f"{at:06X}  {hexes:<{ROW * 3 - 1}}  {ascii_}")
        self.view.setPlainText("\n".join(lines))
        if self._selection is None or not self.follow.isChecked():
            cursor = self.view.textCursor()
            cursor.setPosition(min(caret, len(self.view.toPlainText())))
            self.view.setTextCursor(cursor)
        if self._selection:
            s, e = self._selection
            if self._offset <= s < end:
                row = (s - self._offset) // ROW
                col = (s - self._offset) % ROW
                start = row * LINE_LEN + 8 + col * 3
                n = min(e, end) - s
                rows_span = (col + n - 1) // ROW
                length = n * 3 - 1 + rows_span * (LINE_LEN - ROW * 3)
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
