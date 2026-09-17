"""The translation editor: a text box that completes ``[labels]`` and
commits on Return, and the codes it offers.

Shared by the Strings grid, which opens one in a cell, and the pane under it,
which keeps one open on the selected string.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QWidget

from mapchar.ui.widgets import mono_font


@dataclass(frozen=True)
class CodeInfo:
    """One code of the table set, as the editor offers it."""

    label: str
    operands: str = ""
    """The operand shapes, as the table spells them (``u8``, ``2``…)."""
    uses: int = 0
    """How often the block's strings hold it, for the Insert code buttons."""
    comment: str = ""
    """What the table says the code is, from the comment above its entry."""

    @property
    def completion(self) -> str:
        """What the completion popup lists: the label with its operand shapes,
        and what the table says it is."""
        code = f"[{self.label}{' ' + self.operands if self.operands else ''}]"
        return f"{code}  {self.comment}" if self.comment else code

    @property
    def insertion(self) -> str:
        """What typing it inserts: a code with operands is left open to type in."""
        return f"[{self.label} " if self.operands else f"[{self.label}]"


class CodeEditor(QPlainTextEdit):
    """The translation editor: completes ``[labels]``, commits on Return.

    Return commits and moves on to the next row, Ctrl+Return commits and
    stays — a cell editor's Return belongs to the cell — and Shift+Return
    writes the block's newline code.
    """

    commit = Signal(bool)
    """Committed; the argument says whether to move on to the next row."""
    cancel = Signal()

    def __init__(
        self,
        codes: list[CodeInfo],
        newline_code: str = "[line]",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setFont(mono_font())
        self.setTabChangesFocus(True)
        self.newline_code = newline_code
        """What Shift+Return writes: the block's newline code."""
        self._insertions: dict[str, str] = {}
        self._codes_model = QStringListModel([], self)
        self.completer = QCompleter(self._codes_model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self._insert_completion)
        self.set_codes(codes)

    def set_codes(self, codes: list[CodeInfo]) -> None:
        """The codes ``[`` completes: the table set's."""
        self._insertions = {c.completion: c.insertion for c in codes}
        self._codes_model.setStringList([c.completion for c in codes])

    def _insert_completion(self, chosen: str) -> None:
        text = self._insertions.get(chosen, chosen)
        cursor = self.textCursor()
        start = self._code_start(cursor)
        if start is not None:
            cursor.setPosition(start)
            cursor.setPosition(
                self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor
            )
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def _code_start(self, cursor: QTextCursor) -> int | None:
        """The ``[`` that opens the code the caret is inside, if any."""
        text = self.toPlainText()[: cursor.position()]
        start = text.rfind("[")
        if start < 0 or "]" in text[start:] or (start > 0 and text[start - 1] == "\\"):
            return None
        return start

    def insert_code(self, code: str) -> None:
        self.textCursor().insertText(code)

    def keyPressEvent(self, event) -> None:
        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Escape,
        ):
            event.ignore()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # A line break is a code in the ROM, never a literal newline:
                # the script grammar drops those on the way back in.
                self.insertPlainText(self.newline_code)
            else:
                self.commit.emit(
                    not event.modifiers() & Qt.KeyboardModifier.ControlModifier
                )
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel.emit()
            return
        super().keyPressEvent(event)
        start = self._code_start(self.textCursor())
        if start is None:
            popup.hide()
            return
        prefix = self.toPlainText()[start : self.textCursor().position()]
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            popup.hide()
            return
        rect = self.cursorRect()
        rect.setWidth(
            popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width()
        )
        self.completer.complete(rect)


__all__ = ["CodeEditor", "CodeInfo"]
