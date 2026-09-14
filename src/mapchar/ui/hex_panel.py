"""The Hex panel: a dump of the decoded buffer, with a line to overtype bytes."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontDatabase, QPalette, QTextCursor
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

from mapchar.core.bits import parse_hex
from mapchar.ui import BYTES_PER_ROW, DUMP_WINDOW_BYTES, settings
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.widgets import fit_chars, hint_field

FOLLOW_SELECTION_KEY = "hex/follow_selection"
"""QSettings key for the Follow selection switch.

A local preference like the address format: whether picking bytes in the raw view
should drag the dump along says how you are reading the file right now, not
anything about the file, so it belongs beside the theme and never in the project.
"""

_ADDRESS_GAP = 2
"""Spaces between the address column and the hex cells."""


class _HexView(QPlainTextEdit):
    """The dump: typing a hex digit over a byte overtypes that nibble in place."""

    def __init__(self, panel):
        super().__init__()
        self._panel = panel

    def _byte_at_cursor(self) -> tuple[int, int] | None:
        """``(absolute byte offset, nibble index)`` under the caret, if on hex."""
        pos = self.textCursor().position()
        row, col = divmod(pos, self._panel._line_len)
        hex_start = self._panel._hex_start
        if col < hex_start or col >= hex_start + BYTES_PER_ROW * 3 - 1:
            return None
        rel = col - hex_start
        if rel % 3 == 2:
            return None
        byte = self._panel._offset + row * BYTES_PER_ROW + rel // 3
        if byte >= len(self._panel._data):
            return None
        return byte, rel % 3

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
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
                # The caret's next home is worked out *before* the edit, and
                # handed to the panel: the overtype re-renders through the
                # window, and a caret the render placed from the selection would
                # snap back to the byte just typed over (see HexPanel._render).
                step = 1 if nibble == 0 else 2
                self._panel._pin_caret(at + step)
                self._panel.overtype_requested.emit(offset, bytes([new]))
                return
        super().keyPressEvent(event)


class HexPanel(ThemedIcons, QWidget):
    go_to_requested = Signal(int)
    overtype_requested = Signal(int, bytes)
    find_requested = Signal(str, bool)
    """The find field's text, and whether to search backwards."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._data: bytes = b""
        self._offset = 0
        self._selection: tuple[int, int] | None = None
        self._shown_selection: tuple[int, int] | None = None
        """The selection the caret was last placed on, so a re-render that did
        not move it leaves the caret where the user put it."""
        self._pinned_caret: int | None = None
        """Where the next render must leave the caret — an overtype's own next
        nibble, which outranks both the selection and the caret before it."""
        self._addr_of: Callable[[int], str] = lambda at: f"{at:06X}"
        self._addr_width = 6
        self.view = _HexView(self)
        self.view.setReadOnly(True)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        top = QHBoxLayout()
        # Every field keeps room to show what it holds however narrow the dock
        # gets; the dock's own minimum width follows from them.
        self.goto = hint_field(
            QLineEdit(), "address", "An address to scroll the dump to, then Enter"
        )
        fit_chars(self.goto, 8)
        self.goto.setMaximumWidth(110)
        self.find = hint_field(
            QLineEdit(),
            'hex bytes or "text"',
            'Hex bytes, or "quoted text" through the start table; Enter finds '
            "the next match, Shift+Enter the previous",
        )
        fit_chars(self.find, 12)
        # The same arrow marks the navigation bar and the Preview's pages wear.
        self.find_previous = QPushButton()
        self.find_previous.setToolTip("Find the previous match (Shift+Enter)")
        self.find_next = QPushButton()
        self.find_next.setToolTip("Find the next match (Enter)")
        for button in (self.find_previous, self.find_next):
            button.setFixedWidth(32)
        self._bake_icons()
        self.follow = QCheckBox("Follow selection")
        self.follow.setToolTip(
            "Scroll the dump to whatever is selected in the raw view.\n"
            "Off, the dump stays where you left it."
        )
        self.follow.setChecked(_stored_follow())
        top.addWidget(QLabel("Go to"))
        top.addWidget(self.goto)
        top.addWidget(QLabel("Find"))
        top.addWidget(self.find, 1)
        top.addWidget(self.find_previous)
        top.addWidget(self.find_next)
        top.addWidget(self.follow)
        bottom = QHBoxLayout()
        self.at_label = QLabel("At")
        self.at = hint_field(QLineEdit(), "offset", "The offset to overtype at, in hex")
        fit_chars(self.at, 8)
        self.at.setMaximumWidth(110)
        self.bytes = hint_field(
            QLineEdit(),
            "hex bytes",
            "Hex bytes to write at that offset; Enter or Overtype applies them",
        )
        fit_chars(self.bytes, 12)
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
        self.find.returnPressed.connect(lambda: self._do_find(backwards=False))
        self.find_next.clicked.connect(lambda: self._do_find(backwards=False))
        self.find_previous.clicked.connect(lambda: self._do_find(backwards=True))
        self.bytes.returnPressed.connect(self._on_apply)
        self.apply.clicked.connect(self._on_apply)
        self.follow.toggled.connect(_remember_follow)
        # Shift+Return in the find field searches backwards. It cannot be a
        # ``returnPressed`` connection, which carries no modifiers, and a window
        # shortcut would be taken from the field; the filter sees the press.
        self.find.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if (
            watched is self.find
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._do_find(backwards=True)
            return True
        return super().eventFilter(watched, event)

    def _bake_icons(self) -> None:
        """The find arrows in the theme's button-text color."""
        role = QPalette.ColorRole.ButtonText
        self.find_previous.setIcon(themed_icon(self, Glyph.ARROW_LEFT, role))
        self.find_next.setIcon(themed_icon(self, Glyph.ARROW_RIGHT, role))

    # -- geometry of one rendered line ---------------------------------------
    @property
    def _hex_start(self) -> int:
        """Column the hex cells begin at: the address column plus its gap."""
        return self._addr_width + _ADDRESS_GAP

    @property
    def _line_len(self) -> int:
        """Characters per dump line, newline included."""
        return self._hex_start + BYTES_PER_ROW * 3 - 1 + 2 + BYTES_PER_ROW + 1

    def _pin_caret(self, position: int) -> None:
        self._pinned_caret = position

    def set_data(
        self,
        data: bytes,
        offset: int,
        selection: tuple[int, int] | None,
        addr_of: Callable[[int], str] | None = None,
    ) -> None:
        """The bytes to dump, where from, what is selected, and how to spell an
        address — the last so the dump's own column agrees with the navigation
        bar's address format rather than always showing a flat offset."""
        self._data = data
        self._selection = selection
        if addr_of is not None:
            self._addr_of = addr_of
            self._addr_width = max(len(addr_of(max(len(data) - 1, 0))), 6)
        if selection and self.follow.isChecked():
            offset = max(0, selection[0] - selection[0] % BYTES_PER_ROW)
        self._offset = offset - offset % BYTES_PER_ROW
        if not self.isVisible():
            return
        self._render()

    def refresh(self) -> None:
        self._render()

    def _render(self) -> None:
        caret = self.view.textCursor().position()
        lines = []
        end = min(self._offset + DUMP_WINDOW_BYTES, len(self._data))
        for at in range(self._offset, end, BYTES_PER_ROW):
            chunk = self._data[at : at + BYTES_PER_ROW]
            hexes = " ".join(f"{b:02X}" for b in chunk)
            ascii_ = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
            address = self._addr_of(at)
            lines.append(
                f"{address:<{self._addr_width}}{' ' * _ADDRESS_GAP}"
                f"{hexes:<{BYTES_PER_ROW * 3 - 1}}  {ascii_}"
            )
        self.view.setPlainText("\n".join(lines))
        if self._selection:
            self.at.setText(f"{self._selection[0]:X}")
        # The caret is placed on the selection **only when the selection moved**.
        # Every edit in the window re-renders this panel, and re-seating the caret
        # each time is what made an overtype run impossible: the second digit of a
        # byte, and then the next byte, were typed with the caret snapped back to
        # wherever the raw view's selection happened to be.
        pinned = self._pinned_caret
        self._pinned_caret = None
        if pinned is not None:
            self._place_caret(pinned)
        elif self._selection is not None and self._selection != self._shown_selection:
            self._select_range(*self._selection, end)
        else:
            self._place_caret(caret)
        self._shown_selection = self._selection

    def _place_caret(self, position: int) -> None:
        cursor = self.view.textCursor()
        cursor.setPosition(min(max(position, 0), len(self.view.toPlainText())))
        self.view.setTextCursor(cursor)

    def _select_range(self, start_byte: int, end_byte: int, end: int) -> None:
        """Highlight the dump cells of ``[start_byte, end_byte)``, when on screen."""
        if not (self._offset <= start_byte < end):
            return
        row = (start_byte - self._offset) // BYTES_PER_ROW
        col = (start_byte - self._offset) % BYTES_PER_ROW
        start = row * self._line_len + self._hex_start + col * 3
        n = min(end_byte, end) - start_byte
        rows_span = (col + n - 1) // BYTES_PER_ROW
        length = n * 3 - 1 + rows_span * (self._line_len - BYTES_PER_ROW * 3)
        cursor = self.view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(start + max(length, 2), QTextCursor.MoveMode.KeepAnchor)
        self.view.setTextCursor(cursor)

    def _do_find(self, backwards: bool) -> None:
        text = self.find.text()
        if text.strip():
            self.find_requested.emit(text, backwards)

    def _on_goto(self) -> None:
        try:
            self.go_to_requested.emit(parse_hex(self.goto.text()))
        except ValueError:
            pass

    def _on_apply(self) -> None:
        try:
            at = parse_hex(self.at.text())
            data = bytes.fromhex(self.bytes.text().replace("$", "").replace(",", " "))
        except ValueError:
            return
        if data:
            self.overtype_requested.emit(at, data)


def _stored_follow() -> bool:
    """The remembered Follow selection switch, on by default."""
    value = settings().value(FOLLOW_SELECTION_KEY, True)
    return value if isinstance(value, bool) else str(value).lower() in ("true", "1")


def _remember_follow(on: bool) -> None:
    settings().setValue(FOLLOW_SELECTION_KEY, on)


__all__ = ["FOLLOW_SELECTION_KEY", "HexPanel"]
