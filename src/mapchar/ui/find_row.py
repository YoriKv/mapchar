"""A find field with its two arrows: what the Hex panel and the window's Find
bar both search from.

The field takes hex bytes, or ``"quoted text"`` for the window to run through
the start table; Enter and the right arrow find the next match, Shift+Enter
and the left arrow the previous. The row only asks — :attr:`find_requested`
carries the text and the direction, and the window does the searching.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.widgets import fit_chars, focus_field, hint_field


class FindRow(ThemedIcons, QWidget):
    find_requested = Signal(str, bool)
    """The field's text, and whether to search backwards."""

    def __init__(self, chars: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self.field = hint_field(
            QLineEdit(),
            'hex bytes or "text"',
            'Hex bytes, or "quoted text" through the start table '
            "(Enter: next, Shift+Enter: previous)",
        )
        fit_chars(self.field, chars)
        # The same arrow marks the navigation bar and the Preview's pages wear.
        self.previous = QPushButton()
        self.previous.setToolTip("Find the previous match (Shift+Enter)")
        self.next = QPushButton()
        self.next.setToolTip("Find the next match (Enter)")
        for button in (self.previous, self.next):
            button.setFixedWidth(32)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._bake_icons()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.field, 1)
        layout.addWidget(self.previous)
        layout.addWidget(self.next)
        self.field.returnPressed.connect(lambda: self.search(backwards=False))
        self.next.clicked.connect(lambda: self.search(backwards=False))
        self.previous.clicked.connect(lambda: self.search(backwards=True))
        # Shift+Return in the field searches backwards. It cannot be a
        # ``returnPressed`` connection, which carries no modifiers, and a window
        # shortcut would be taken from the field; the filter sees the press.
        self.field.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if (
            watched is self.field
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.search(backwards=True)
            return True
        return super().eventFilter(watched, event)

    def _bake_icons(self) -> None:
        """The arrows in the theme's button-text color."""
        role = QPalette.ColorRole.ButtonText
        self.previous.setIcon(themed_icon(self, Glyph.ARROW_LEFT, role))
        self.next.setIcon(themed_icon(self, Glyph.ARROW_RIGHT, role))

    def text(self) -> str:
        """What is in the field, trimmed."""
        return self.field.text().strip()

    def set_text(self, text: str) -> None:
        self.field.setText(text)

    def focus(self) -> None:
        """Put the keyboard in the field, with what it holds selected, so
        typing replaces the last search and Enter repeats it."""
        focus_field(self.field)

    def search(self, backwards: bool) -> None:
        if self.text():
            self.find_requested.emit(self.text(), backwards)
