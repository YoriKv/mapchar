"""The Hex panel: a dump of the decoded buffer, with a line to overtype bytes."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.bits import parse_hex_bytes
from mapchar.ui import (
    BYTES_PER_ROW,
    DUMP_WINDOW_BYTES,
    set_setting_bool,
    setting_bool,
    theme,
)
from mapchar.ui.find_row import FindRow
from mapchar.ui.number_fields import AddressEdit, AddressSpelling
from mapchar.ui.widgets import fit_chars, hint_field, mono_font

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


class HexPanel(QWidget):
    go_to_requested = Signal(int)
    overtype_requested = Signal(int, bytes)
    find_requested = Signal(str, bool)
    """The find field's text, and whether to search backwards."""

    def __init__(
        self, spelling: AddressSpelling | None = None, parent: QWidget | None = None
    ):
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
        self._spelling = spelling
        """How the address column is spelled: the window's one spelling, so the
        dump agrees with the navigation bar rather than always showing a flat
        offset. Without one, flat hex."""
        self._rendered: tuple[bytes, int, int, int, list[str]] | None = None
        """What the dump's text was last built from — the bytes, the window,
        the address column's width and every address in it — so a render that
        would build the same text, as every move of the selection would, keeps
        it."""
        self.view = _HexView(self)
        self.view.setReadOnly(True)
        self.view.setFont(mono_font())
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        top = QHBoxLayout()
        # Every field keeps room to show what it holds however narrow the dock
        # gets; the dock's own minimum width follows from them.
        self.goto = hint_field(
            AddressEdit(spelling),
            "address",
            "Move the view to this address (Enter)",
        )
        # The same field and arrows as the window's Find bar; a search from
        # here is the window's current search too.
        self.find_row = FindRow()
        self.find = self.find_row.field
        self.follow = QCheckBox("Follow selection")
        self.follow.setToolTip(
            "Start the dump at the selection; off, at the view's position"
        )
        self.follow.setChecked(setting_bool(FOLLOW_SELECTION_KEY, True))
        top.addWidget(QLabel("Go to"))
        top.addWidget(self.goto)
        top.addWidget(QLabel("Find"))
        top.addWidget(self.find_row, 1)
        top.addWidget(self.follow)
        bottom = QHBoxLayout()
        self.at_label = QLabel("At")
        self.at = hint_field(
            AddressEdit(self.goto.spelling), "address", "The address to overtype at"
        )
        self.bytes = hint_field(
            QLineEdit(),
            "hex bytes",
            "Hex bytes to write at that address (Enter)",
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
        self.find_row.find_requested.connect(self.find_requested)
        self.bytes.returnPressed.connect(self._on_apply)
        self.apply.clicked.connect(self._on_apply)
        self.follow.toggled.connect(
            lambda on: set_setting_bool(FOLLOW_SELECTION_KEY, on)
        )
        if spelling is not None:
            # The address format changed under the dump: the column it spells
            # has to be built again, and nothing else asks it to.
            spelling.changed.connect(self._on_spelling_changed)

    def _on_spelling_changed(self, _old) -> None:
        if self.isVisible():
            self._render()

    # -- geometry of one rendered line ---------------------------------------
    def _addr_of(self, at: int) -> str:
        """One address as the dump's column spells it."""
        return self._spelling.format(at) if self._spelling is not None else f"{at:06X}"

    @property
    def _addr_width(self) -> int:
        """How wide the address column is: as wide as the file's last address,
        and never narrower than a flat six-digit offset."""
        return max(len(self._addr_of(max(len(self._data) - 1, 0))), 6)

    @property
    def _hex_start(self) -> int:
        """Column the hex cells begin at: the address column plus its gap."""
        return self._addr_width + _ADDRESS_GAP

    @property
    def _ascii_start(self) -> int:
        """Column the ASCII cells begin at: past the hex cells and their gap."""
        return self._hex_start + BYTES_PER_ROW * 3 - 1 + 2

    @property
    def _line_len(self) -> int:
        """Characters per dump line, newline included."""
        return self._ascii_start + BYTES_PER_ROW + 1

    def _pin_caret(self, position: int) -> None:
        self._pinned_caret = position

    def set_data(
        self,
        data: bytes,
        offset: int,
        selection: tuple[int, int] | None,
    ) -> None:
        """The bytes to dump, where from, and what is selected."""
        self._data = data
        self._selection = selection
        if selection and self.follow.isChecked():
            offset = max(0, selection[0] - selection[0] % BYTES_PER_ROW)
        self._offset = offset - offset % BYTES_PER_ROW
        if not self.isVisible():
            return
        self._render()

    def _render(self) -> None:
        caret = self.view.textCursor().position()
        end = min(self._offset + DUMP_WINDOW_BYTES, len(self._data))
        rows = range(self._offset, end, BYTES_PER_ROW)
        addresses = [self._addr_of(at) for at in rows]
        built = (self._data, self._offset, end, self._addr_width, addresses)
        if self._rendered is None or any(
            a is not b and a != b for a, b in zip(built, self._rendered, strict=True)
        ):
            self._rendered = built
            lines = []
            for at, address in zip(rows, addresses, strict=True):
                chunk = self._data[at : at + BYTES_PER_ROW]
                hexes = " ".join(f"{b:02X}" for b in chunk)
                ascii_ = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
                lines.append(
                    f"{address:<{self._addr_width}}{' ' * _ADDRESS_GAP}"
                    f"{hexes:<{BYTES_PER_ROW * 3 - 1}}  {ascii_}"
                )
            self.view.setPlainText("\n".join(lines))
        if self._selection:
            self.at.set_value(self._selection[0])
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
            self._caret_to_byte(self._selection[0], end)
        else:
            self._place_caret(caret)
        self._shown_selection = self._selection
        self._highlight_selection(end)

    def _place_caret(self, position: int) -> None:
        cursor = self.view.textCursor()
        last = self.view.document().characterCount() - 1
        cursor.setPosition(min(max(position, 0), last))
        self.view.setTextCursor(cursor)

    def _caret_to_byte(self, byte: int, end: int) -> None:
        """Put the caret on a byte's hex cell, when on screen."""
        if self._offset <= byte < end:
            row, col = divmod(byte - self._offset, BYTES_PER_ROW)
            self._place_caret(row * self._line_len + self._hex_start + col * 3)

    def _highlight_selection(self, end: int) -> None:
        """Tint the selected bytes' hex and ASCII cells, as the central view's tabs
        tint theirs.

        A tint rather than the box's own selection, which the caret would clear
        and an unfocused box draws faintly or not at all; the part of the
        selection inside the window is tinted, wherever it starts.
        """
        highlights = []
        if self._selection is not None:
            tint = QTextCharFormat()
            tint.setBackground(theme.TINT_SELECTION)
            at, stop = (
                max(self._selection[0], self._offset),
                min(self._selection[1], end),
            )
            while at < stop:
                row, col = divmod(at - self._offset, BYTES_PER_ROW)
                count = min(stop - at, BYTES_PER_ROW - col)
                line = row * self._line_len
                for start, length in (
                    (line + self._hex_start + col * 3, count * 3 - 1),
                    (line + self._ascii_start + col, count),
                ):
                    cursor = QTextCursor(self.view.document())
                    cursor.setPosition(start)
                    cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
                    highlight = QTextEdit.ExtraSelection()
                    highlight.cursor = cursor
                    highlight.format = tint
                    highlights.append(highlight)
                at += count
        self.view.setExtraSelections(highlights)

    def _on_goto(self) -> None:
        offset = self.goto.value()
        if offset is not None:
            self.go_to_requested.emit(offset)

    def _on_apply(self) -> None:
        at = self.at.value()
        try:
            data = parse_hex_bytes(self.bytes.text())
        except ValueError:
            return
        if at is not None and data:
            self.overtype_requested.emit(at, data)


__all__ = ["FOLLOW_SELECTION_KEY", "HexPanel"]
