"""The project's glossary: its panel, its edits as undo steps, typing a term's
translation into the string being edited, and what is asked of a term."""

from __future__ import annotations

from mapchar.core.errors import MapcharError
from mapchar.project.glossary import (
    GlossaryTerm,
    glossary_from_text,
    glossary_text,
    merged_terms,
    missing_terms,
    term_uses,
)
from mapchar.ui.find_replace import BLOCK, PROJECT, SELECTION
from mapchar.ui.undo_commands import GlossaryCommand

GLOSSARY_FILES = "Tab-separated (*.tsv *.txt);;Comma-separated (*.csv)"


def _delimiter(path: str) -> str:
    return "," if path.lower().endswith(".csv") else "\t"


class GlossaryMixin:
    """The project's glossary: its panel, its edits as undo steps, typing a
    term's translation into the string being edited, and what is asked of a
    term.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_glossary(self) -> None:
        self.glossary_dock.show()
        self.glossary_dock.raise_()
        self._sync_glossary()

    def _sync_glossary(self) -> None:
        """The panel on the project's terms and the string on screen: what it
        lists as *in this string*. Nothing while it is hidden."""
        panel = self.glossary_panel
        if self.glossary_dock.isHidden():
            return
        panel.set_terms(self.workspace.glossary)
        entry = self._entry
        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        panel.set_context(rec.original if rec is not None else "")
        panel.set_unspelled(self._unspelled_terms())

    def _unspelled_terms(self) -> dict[GlossaryTerm, str]:
        """The terms whose translation the block on screen cannot encode, with
        why: known before a replace runs into it one string at a time."""
        entry = self._current_block(need_doc=True)
        tables = self._table_set_of(entry) if entry is not None else None
        if entry is None or entry.config is None or tables is None:
            return {}
        key = (tables.start.id, sum(len(t.entries) for t in tables.tables.values()))
        cached = self._unspelled_cache
        terms = tuple(self.workspace.glossary)
        if cached is not None and cached[0] == (key, terms):
            return cached[1]
        from mapchar.engines.encode import encode

        found = {}
        for term in terms:
            if not term.translation:
                continue
            try:
                # Judged as text inside a string, which brings its own end.
                encode(term.translation, tables, end_terminated=False)
            except MapcharError as exc:
                found[term] = str(exc)
        self._unspelled_cache = ((key, terms), found)
        return found

    def _on_glossary_changed(self, terms: list[GlossaryTerm]) -> None:
        if terms == self.workspace.glossary:
            return
        self._push_command(GlossaryCommand(self, self.workspace.glossary, terms))

    def apply_glossary(self, terms: list[GlossaryTerm]) -> None:
        """Land one side of a glossary edit."""
        self.workspace.glossary = list(terms)
        self._glossary_changed()
        self._update_title()

    def _glossary_changed(self, refresh: bool = True) -> None:
        """The terms are other terms: every surface that reads them catches up
        — the panel, the pane's underlines, the terms Find and Replace offers
        and, unless a project load is about to lay the view out anyway, the
        rows' missing terms."""
        self._sync_glossary()
        self.strings.set_terms(self.workspace.glossary)
        self.find_replace.set_terms(self.workspace.glossary)
        if not refresh:
            return
        if self._entry is not None and self._entry.doc is not None:
            self._refresh_view()
        self._refresh_project_strings()

    def _insert_glossary(self, text: str) -> None:
        """Type a term's translation where the translation is being edited."""
        if self._current_block(need_doc=True) is None:
            return
        self._show_view("strings")
        self.strings.insert_text(text)
        self.activateWindow()

    # --- what a row says of the glossary ------------------------------------------

    def _missing_terms(self, rec) -> str:
        """The terms the string's original holds that its translation has some
        other way than the glossary does, as a row says it; nothing for a
        string nobody has translated."""
        if not self.workspace.glossary:
            return ""
        if not rec.edited and rec.unwritten is None:
            return ""
        missed = missing_terms(self.workspace.glossary, rec.original, rec.shown_text())
        return ", ".join(f"{t.term} → {t.translation}" for t in missed)

    # --- asked of a term ------------------------------------------------------------

    def _add_to_glossary(self, term: str, translation: str) -> None:
        """A new term from what is marked in the pane, opened in the panel on
        whichever half is still to be typed."""
        self._show_glossary()
        known = next(
            (t for t in self.workspace.glossary if t.term == term and term), None
        )
        if known is not None:
            self.statusBar().showMessage(f"{term} is in the glossary already", 4000)
            return
        self.glossary_panel.add_term(term, translation)

    def _replace_glossary_terms(
        self, term: GlossaryTerm | None = None, scope: str | None = None
    ) -> None:
        """Find and Replace, opened on the glossary's terms."""
        if not any(t.translation for t in self.workspace.glossary):
            self.statusBar().showMessage("The glossary has no translated terms", 4000)
            return
        if scope is None:
            scope = PROJECT if term is not None else BLOCK
        self._show_find_replace(glossary=True, term=term, scope=scope)

    def _replace_terms_in_selection(self) -> None:
        self._replace_glossary_terms(scope=SELECTION)

    def _show_term_strings(self, term: GlossaryTerm) -> None:
        """Project Strings, filtered to the strings that hold the term."""
        self._show_project_strings()
        self.project_strings.filter.setText(term.term)

    def _count_glossary_uses(self) -> None:
        """How many of the project's strings hold each term: every block is
        read for it."""
        originals = [rec.original for _, rec in self._all_block_strings()]
        self.glossary_panel.set_uses(term_uses(self.workspace.glossary, originals))
        self.statusBar().showMessage(
            f"Counted {len(self.workspace.glossary)} term(s) over "
            f"{len(originals)} string(s)",
            4000,
        )

    # --- shared between projects ------------------------------------------------------

    def _import_glossary(self) -> None:
        path = self._pick_open("Import Glossary", GLOSSARY_FILES)
        if path is None:
            return
        self._remember_dir(path)
        text, notices = self._read_text(path)
        if text is None:
            return
        incoming = glossary_from_text(text, _delimiter(path))
        before = self.workspace.glossary
        merged = merged_terms(before, incoming)
        known = {t.term for t in before}
        added = sum(1 for t in incoming if t.term not in known)
        if merged != before:
            self._push_command(GlossaryCommand(self, before, merged))
        self._report(
            "Glossary Imported",
            f"{len(incoming)} term(s) read: {added} new, "
            f"{len(incoming) - added} laid over ones already here",
            notices,
        )

    def _export_glossary(self) -> None:
        path = self._pick_save("Export Glossary", "glossary.tsv", GLOSSARY_FILES)
        if path is None:
            return
        self._remember_dir(path)
        text = glossary_text(self.workspace.glossary, _delimiter(path))
        if self._write_text(path, text):
            self.statusBar().showMessage(
                f"Wrote {len(self.workspace.glossary)} term(s) to {path}", 4000
            )
