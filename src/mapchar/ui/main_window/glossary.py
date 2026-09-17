"""The project's glossary: its window, its edits as undo steps, and typing a
term's translation into the string being edited."""

from __future__ import annotations

from mapchar.project.glossary import GlossaryTerm
from mapchar.ui.undo_commands import GlossaryCommand


class GlossaryMixin:
    """The project's glossary: its window, its edits as undo steps, and typing
    a term's translation into the string being edited.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_glossary(self) -> None:
        window = self.glossary_window
        window.show()
        window.raise_()
        window.activateWindow()
        self._sync_glossary()

    def _sync_glossary(self) -> None:
        """The window on the project's terms and the string on screen: what it
        lists as *in this string*. Nothing while it is hidden."""
        window = self.glossary_window
        if not window.isVisible():
            return
        window.set_terms(self.workspace.glossary)
        entry = self._entry
        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        window.set_context(rec.original if rec is not None else "")

    def _on_glossary_changed(self, terms: list[GlossaryTerm]) -> None:
        if terms == self.workspace.glossary:
            return
        self._push_command(GlossaryCommand(self, self.workspace.glossary, terms))

    def apply_glossary(self, terms: list[GlossaryTerm]) -> None:
        """Land one side of a glossary edit."""
        self.workspace.glossary = list(terms)
        if self.glossary_window.isVisible():
            self.glossary_window.set_terms(self.workspace.glossary)
        self._update_title()

    def _insert_glossary(self, text: str) -> None:
        """Type a term's translation where the translation is being edited."""
        if self._current_block(need_doc=True) is None:
            return
        self._show_view("strings")
        self.strings.insert_text(text)
        self.activateWindow()
