"""The editing pane under the Strings grid: one string, read whole.

The grid cuts Original and Translation short to one line each; the pane shows
the selected string's original with its line breaks, beside a multi-line
editor on what the bytes say, with the byte readout and the notes. Codes are
dimmed in both, and hidden in the original on request, as the Text tab hides
them. The editor is the grid's own :class:`~mapchar.ui.code_editor.CodeEditor`:
the same completion, the same keys.

Presentation only: the pane holds the text it was given and the text typed,
and hands a commit to whoever owns it (:attr:`StringPane.committer`), keeping
the draft when it is refused.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import (
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mapchar.ui import theme
from mapchar.ui.code_editor import CodeEditor, CodeInfo
from mapchar.ui.token_text import CODE_IN_TEXT, hide_codes
from mapchar.ui.widgets import ElidedLabel, apply_wrap, mono_font, setting_toggle

if TYPE_CHECKING:
    from mapchar.ui.strings_view import RowData

SHOW_CODES_KEY = "view/strings_show_codes"
WRAP_KEY = "view/strings_wrap"


class CodeHighlighter(QSyntaxHighlighter):
    """Dims the codes in script text, so the words read through them."""

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        fmt = QTextCharFormat()
        fmt.setForeground(
            QApplication.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            )
        )
        for match in CODE_IN_TEXT.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), fmt)


class StringPane(QWidget):
    draft_changed = Signal(str)
    """The editor's text as it is typed."""
    notes_edited = Signal(int, str)
    """The notes field left with other text: the string's index and the text."""
    advance_requested = Signal()
    """Return committed: move on to the next row, keeping the editor's focus."""
    problem_shown = Signal(str)
    """A commit was refused, with why."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.index: int | None = None
        """The string shown, or none."""
        self._text = ""
        """What the bytes say, as last loaded: what the editor is dirty against."""
        self._original = ""
        self.committer: Callable[[int, str], str | None] | None = None
        """What a commit hands the text to: ``None`` when it landed, else why
        it did not, which keeps the draft."""

        self.heading = ElidedLabel("")
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setFont(mono_font())
        self.original.setUndoRedoEnabled(False)
        self.original.setPlaceholderText("Original")
        self._original_codes = CodeHighlighter(self.original.document())
        self.show_codes = setting_toggle(
            "Show codes",
            "Show codes like [end] or [color 3] in the original; "
            "hidden, their line breaks stay",
            SHOW_CODES_KEY,
            lambda _on: self._render_original(),
        )
        self.wrap = setting_toggle(
            "Wrap",
            "Wrap long lines to the pane's width",
            WRAP_KEY,
            self._apply_wrap,
        )
        left_bar = QHBoxLayout()
        left_bar.setContentsMargins(0, 0, 0, 0)
        left_bar.addWidget(self.heading, 1)
        left_bar.addWidget(self.show_codes)
        left_bar.addWidget(self.wrap)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(left_bar)
        ll.addWidget(self.original, 1)

        self.readout = ElidedLabel("")
        self.editor = CodeEditor([], "[line]")
        self.editor.setPlaceholderText("Translation")
        self._editor_codes = CodeHighlighter(self.editor.document())
        self.editor.installEventFilter(self)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes")
        self.notes.setClearButtonEnabled(True)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.readout)
        rl.addWidget(self.editor, 1)
        rl.addWidget(self.notes)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setChildrenCollapsible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

        self._apply_wrap(self.wrap.isChecked())
        self.editor.textChanged.connect(self._on_typed)
        self.editor.commit.connect(self._commit)
        self.editor.cancel.connect(self.cancel)
        self.notes.editingFinished.connect(self._on_notes)
        self.set_row(None)

    # --- what is shown -----------------------------------------------------

    def set_codes(self, codes: list[CodeInfo]) -> None:
        self.editor.set_codes(codes)

    def set_newline_code(self, code: str) -> None:
        self.editor.newline_code = code

    def set_row(self, data: RowData | None) -> None:
        """Show one string, or none. A draft on the same string is kept: the
        grid refreshes its rows under an editor for many reasons — another
        cell landed, an undo — and none of them is a reason to lose typing.
        """
        same = data is not None and data.index == self.index
        keep = same and self.dirty()
        self.index = data.index if data is not None else None
        self.setEnabled(data is not None)
        if data is None:
            self._text = self._original = ""
            self.heading.setText("")
            self._set_readout("", problem=False)
            self.original.setPlainText("")
            self._load("")
            self.notes.setText("")
            return
        self._original = data.original
        self.heading.setText(f"#{data.index} · {data.address:X} · {data.status}")
        self._render_original()
        self._text = data.translation
        if not keep:
            self._load(data.translation)
            self._set_readout(f"{data.used} / {data.room} byte(s)", problem=False)
        self.notes.setText(data.notes)

    def _render_original(self) -> None:
        text = self._original
        if not self.show_codes.isChecked():
            text = hide_codes(text)
        self.original.setPlainText(text)

    def _load(self, text: str) -> None:
        was = self.editor.blockSignals(True)
        try:
            self.editor.setPlainText(text)
            self.editor.moveCursor(QTextCursor.MoveOperation.End)
        finally:
            self.editor.blockSignals(was)

    def _apply_wrap(self, on: bool) -> None:
        apply_wrap(self.original, on)
        apply_wrap(self.editor, on)

    def set_readout(self, text: str, problem: bool = False) -> None:
        """The byte budget of the draft, or why a commit was refused."""
        self._set_readout(text, problem)

    def _set_readout(self, text: str, problem: bool) -> None:
        palette = self.readout.palette()
        ink = (
            theme.ERROR_INK
            if problem
            else self.palette().color(QPalette.ColorRole.WindowText)
        )
        palette.setColor(QPalette.ColorRole.WindowText, ink)
        self.readout.setPalette(palette)
        self.readout.setText(text)

    # --- the draft ----------------------------------------------------------

    def text(self) -> str:
        return self.editor.toPlainText()

    def dirty(self) -> bool:
        """Whether the editor says something other than the bytes do."""
        return self.index is not None and self.editor.toPlainText() != self._text

    def _on_typed(self) -> None:
        self.draft_changed.emit(self.editor.toPlainText())

    def cancel(self) -> None:
        """Esc: the draft goes, the bytes' text comes back."""
        self._load(self._text)
        self.draft_changed.emit(self._text)

    def _commit(self, advance: bool) -> None:
        if self.flush() and advance:
            self.advance_requested.emit()

    def flush(self) -> bool:
        """Land the draft, if there is one: ``True`` when the editor and the
        bytes agree afterwards, ``False`` when the commit was refused and the
        draft stays."""
        if not self.dirty():
            return True
        if self.committer is None:
            return True
        text = self.editor.toPlainText()
        problem = self.committer(self.index, text)
        if problem:
            self._set_readout(problem, problem=True)
            self.problem_shown.emit(problem)
            return False
        # Landed: the bytes say this now, whether or not a row refresh follows.
        self._text = text
        return True

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        # Leaving the editor lands the draft, as leaving a cell does; a popup
        # taking focus — the completion list — is not leaving.
        if (
            watched is self.editor
            and event.type() == QEvent.Type.FocusOut
            and event.reason() != Qt.FocusReason.PopupFocusReason
        ):
            self.flush()
        return super().eventFilter(watched, event)

    def _on_notes(self) -> None:
        if self.index is not None:
            self.notes_edited.emit(self.index, self.notes.text())


__all__ = ["CodeHighlighter", "StringPane"]
