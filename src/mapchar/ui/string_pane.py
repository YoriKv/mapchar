"""The editing pane under the Strings grid: one string, read whole.

The grid cuts Original and Translation short to one line each; the pane shows
the selected string's original with its line breaks, beside a multi-line
editor on what the bytes say, with the byte readout and the notes. Codes are
dimmed in both, and hidden in the original on request, as the Text tab hides
them. The editor is the grid's own :class:`~mapchar.ui.code_editor.CodeEditor`:
the same completion, the same keys.

Presentation only: the pane holds the text it was given and the text typed,
and hands a commit to whoever owns it (:attr:`StringPane.committer`), which
keeps a text the bytes refuse.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
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
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from mapchar.project.glossary import GlossaryTerm, TermHit, term_hits
from mapchar.ui import theme
from mapchar.ui.code_editor import CodeEditor, CodeInfo
from mapchar.ui.token_text import code_spans, hide_codes
from mapchar.ui.widgets import ElidedLabel, apply_wrap, mono_font, setting_toggle

if TYPE_CHECKING:
    from mapchar.ui.strings_view import RowData

SHOW_CODES_KEY = "view/strings_show_codes"
WRAP_KEY = "view/strings_wrap"


def u16(text: str, index: int) -> int:
    """Character ``index`` of ``text`` as a position in a text document, which
    counts a character outside the basic plane as two."""
    return len(text[:index].encode("utf-16-le")) // 2


class CodeHighlighter(QSyntaxHighlighter):
    """Dims the codes in script text, so the words read through them."""

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        fmt = QTextCharFormat()
        fmt.setForeground(
            QApplication.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            )
        )
        for start, stop in code_spans(text):
            self.setFormat(start, stop - start, fmt)


class StringPane(QWidget):
    draft_changed = Signal(str)
    """The editor's text as it is typed."""
    notes_edited = Signal(int, str)
    """The notes field left with other text: the string's index and the text."""
    advance_requested = Signal()
    """Return committed: move on to the next row, keeping the editor's focus."""
    problem_shown = Signal(str)
    """A commit was refused, with why."""
    glossary_add_requested = Signal(str, str)
    """Add a term to the glossary: what is marked in the original, and what is
    marked in the translation."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.index: int | None = None
        """The string shown, or none."""
        self._text = ""
        """What the string says, as last loaded — its bytes, or the translation
        it keeps unwritten: what the editor is dirty against."""
        self._original = ""
        self._terms: list[GlossaryTerm] = []
        self._term_hits: list[TermHit] = []
        """The glossary's terms in the original as it is shown, underlined."""
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
        for box in (self.original, self.editor):
            box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            box.customContextMenuRequested.connect(
                lambda pos, box=box: self._on_menu(box, pos)
            )
        self.original.viewport().installEventFilter(self)
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
        self._text = data.shown
        if not keep:
            self._load(data.shown)
            if data.unwritten is not None and data.problem:
                self._set_readout(f"Not written — {data.problem}", problem=True)
            else:
                self._set_readout(f"{data.used} / {data.room} byte(s)", problem=False)
        self.notes.setText(data.notes)

    def _render_original(self) -> None:
        text = self._original
        if not self.show_codes.isChecked():
            text = hide_codes(text)
        self.original.setPlainText(text)
        self._mark_terms()

    def set_terms(self, terms: list[GlossaryTerm]) -> None:
        """The glossary's terms: underlined where the original holds one, with
        its translation on hover."""
        self._terms = list(terms)
        self._mark_terms()

    def _mark_terms(self) -> None:
        text = self.original.toPlainText()
        self._term_hits = term_hits(self._terms, text)
        fmt = QTextCharFormat()
        fmt.setFontUnderline(True)
        fmt.setUnderlineColor(theme.DONE_INK)
        marks = []
        for hit in self._term_hits:
            mark = QTextEdit.ExtraSelection()
            mark.format = fmt
            mark.cursor = QTextCursor(self.original.document())
            mark.cursor.setPosition(u16(text, hit.start))
            mark.cursor.setPosition(
                u16(text, hit.stop), QTextCursor.MoveMode.KeepAnchor
            )
            marks.append(mark)
        self.original.setExtraSelections(marks)

    def _term_tip(self, pos: QPoint) -> str:
        """What hovering the original at ``pos`` says: the term there, what it
        becomes and its notes."""
        text = self.original.toPlainText()
        at = self.original.cursorForPosition(pos).position()
        for hit in self._term_hits:
            if u16(text, hit.start) <= at < u16(text, hit.stop):
                term = hit.term
                tip = f"{term.term} → {term.translation or '(no translation yet)'}"
                return f"{tip}\n{term.notes}" if term.notes else tip
        return ""

    def select_span(self, start: int, stop: int) -> None:
        """Mark characters ``start``–``stop`` of the editor's text: the hit a
        search stands on."""
        text = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        cursor.setPosition(u16(text, start))
        cursor.setPosition(u16(text, stop), QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()

    def selected_text(self) -> tuple[str, str]:
        """What is marked in the original, and in the editor."""

        def marked(box: QPlainTextEdit) -> str:
            return box.textCursor().selectedText().replace("\u2029", "\n").strip()

        return marked(self.original), marked(self.editor)

    def _on_menu(self, box: QPlainTextEdit, pos: QPoint) -> None:
        """The box's own menu, and the way into the glossary under it."""
        menu = box.createStandardContextMenu()
        menu.addSeparator()
        term, translation = self.selected_text()
        add = menu.addAction(
            "Add to &Glossary…",
            lambda: self.glossary_add_requested.emit(term, translation),
        )
        add.setEnabled(bool(term or translation))
        menu.exec(box.viewport().mapToGlobal(pos))
        menu.deleteLater()

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
        bytes agree afterwards, ``False`` when the bytes refused it — the
        string then keeps it unwritten, so it is no draft any more either."""
        if not self.dirty():
            return True
        if self.committer is None:
            return True
        text = self.editor.toPlainText()
        problem = self.committer(self.index, text)
        # Landed or kept: the string says this now, whether or not a row
        # refresh follows.
        self._text = text
        if problem:
            self._set_readout(problem, problem=True)
            self.problem_shown.emit(problem)
            return False
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
        if watched is self.original.viewport() and event.type() == QEvent.Type.ToolTip:
            tip = self._term_tip(event.pos())
            if tip:
                QToolTip.showText(event.globalPos(), tip, self.original)
                return True
        return super().eventFilter(watched, event)

    def _on_notes(self) -> None:
        if self.index is not None:
            self.notes_edited.emit(self.index, self.notes.text())


__all__ = ["CodeHighlighter", "StringPane"]
