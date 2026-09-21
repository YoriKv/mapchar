"""Find and Replace over the strings' text: what is typed, or the glossary's
terms."""

from __future__ import annotations

from collections.abc import Iterator

from mapchar.core.block import Status
from mapchar.engines import scriptfind
from mapchar.project.entry import Entry
from mapchar.project.glossary import GlossaryTerm, replace_terms, term_hits
from mapchar.ui.find_replace import BLOCK, PROJECT, SELECTION, Search

Hit = tuple[int, int, str, str]
"""Where a match starts and stops in a string's text, what goes in its place,
and how the dialog names it."""


class FindReplaceMixin:
    """Find and Replace over the strings' text.

    The text searched is what the translator has each string say
    (:meth:`~mapchar.core.block.StringRecord.shown_text`), and a hit is stood
    on — its string selected, its span marked in the pane — before Replace
    puts anything in its place, so every one is seen on the way through.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_find_replace(
        self,
        glossary: bool = False,
        term: GlossaryTerm | None = None,
        scope: str | None = None,
    ) -> None:
        """Open the dialog: on typed text, or on the glossary's terms — one of
        them or all — and over ``scope`` when one is asked for. The strings
        selected now are what *Selected strings* means until it is opened
        again."""
        dialog = self.find_replace
        self._fr_selection = (self._entry, frozenset(self.strings.selected_indices()))
        self._fr_at = None
        dialog.set_terms(self.workspace.glossary)
        dialog.set_glossary(glossary, term)
        if scope is not None:
            dialog.set_scope(scope)
        dialog.show_hit("")
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if not glossary:
            dialog.find.setFocus()

    # --- what is searched ------------------------------------------------------

    def _fr_blocks(self, scope: str) -> list[Entry]:
        """The blocks a search runs over, the current one first.

        In project scope every block is read first, so a block the user has
        not opened yet is searched too.
        """
        current = self._current_block(need_doc=True)
        if scope == SELECTION:
            entry = self._fr_selection[0]
            return [entry] if entry is not None and entry.doc is not None else []
        if scope != PROJECT:
            return [current] if current is not None else []
        blocks = [e for e, doc in self._readable_blocks() if doc.strings]
        if current in blocks:
            blocks.remove(current)
            blocks.insert(0, current)
        return blocks

    def _fr_strings(self, search: Search) -> list[tuple[Entry, object]]:
        """Every ``(block, string)`` the search may touch, in order."""
        chosen = self._fr_selection[1] if search.scope == SELECTION else None
        return [
            (e, r)
            for e in self._fr_blocks(search.scope)
            for r in e.doc.strings
            if (chosen is None or r.index in chosen)
            and not (search.skip_done and r.status is Status.DONE)
        ]

    def _fr_hit(self, search: Search, text: str, start: int = 0) -> Hit | None:
        """The first match in ``text`` at or after ``start``."""
        if search.glossary:
            terms = [search.term] if search.term else self.workspace.glossary
            for hit in term_hits(terms, text, translated_only=True):
                if hit.start >= start:
                    term = hit.term
                    return (
                        hit.start,
                        hit.stop,
                        term.translation,
                        f"{term.term} → {term.translation}",
                    )
            return None
        span = scriptfind.find(text, search.needle, case=search.case, start=start)
        return None if span is None else (*span, search.replacement, "")

    def _fr_replaced(self, search: Search, text: str) -> tuple[str, int]:
        """``text`` with every match replaced, and how many went."""
        if search.glossary:
            terms = [search.term] if search.term else self.workspace.glossary
            return replace_terms(terms, text)
        return scriptfind.replace(
            text, search.needle, search.replacement, case=search.case
        )

    def _fr_walk(self, search: Search) -> Iterator[tuple[Entry, object, int]]:
        """The strings to look through and where in each to start: on from the
        hit stood on — else from the selected string — and round to it again."""
        pairs = self._fr_strings(search)
        if not pairs:
            return
        at = self._fr_at
        selected = self.strings.selected_indices()
        here = (at[0], at[1]) if at else (self._entry, selected[0] if selected else -1)
        first = next(
            (n for n, (e, r) in enumerate(pairs) if (e, r.index) == here),
            None,
        )
        if first is None:
            yield from ((e, r, 0) for e, r in pairs)
            return
        entry, rec = pairs[first]
        yield entry, rec, at[2] if at else 0
        yield from ((e, r, 0) for e, r in pairs[first + 1 :] + pairs[:first])
        yield entry, rec, 0

    # --- the three buttons -------------------------------------------------------

    def _search_next(self, search: Search, resume: int | None = None) -> bool:
        """Stand on the next hit; ``False`` when there is none."""
        if not search.glossary and not search.needle:
            return False
        selected = self.strings.selected_indices()
        at = self._fr_at
        if at is not None and (at[0] is not self._entry or selected[:1] != [at[1]]):
            # The selection has been moved by hand since: on from there.
            self._fr_at = None
        if resume is not None and self._fr_at is not None:
            self._fr_at = (*self._fr_at[:2], resume)
        elif self._fr_at is not None:
            self._fr_at = (*self._fr_at[:2], self._fr_at[2] + 1)
        for entry, rec, start in self._fr_walk(search):
            hit = self._fr_hit(search, rec.shown_text(), start)
            if hit is None:
                continue
            if entry is not self._entry:
                self._activate_entry(entry)
            self._show_view("strings")
            self.strings.select_index(rec.index)
            self._on_string_row(rec.index)
            self.strings.select_span(hit[0], hit[1])
            self._fr_at = (entry, rec.index, hit[0])
            self.find_replace.show_hit(hit[3])
            return True
        self._fr_at = None
        self.find_replace.show_hit("")
        self.statusBar().showMessage("Not found", 3000)
        return False

    def _search_replace(self, search: Search) -> None:
        """Replace the hit stood on, and stand on the next; with none stood
        on, find the first."""
        at = self._fr_at
        rec = self._string(at[0], at[1]) if at else None
        hit = self._fr_hit(search, rec.shown_text(), at[2]) if rec else None
        if hit is None or hit[0] != at[2]:
            self._search_next(search)
            return
        entry, index, start = at
        text = rec.shown_text()
        problems = self._set_translation(
            entry, index, text[:start] + hit[2] + text[hit[1] :]
        )
        if problems:
            self._refuse_edit(problems)
        self._search_next(search, resume=start + len(hit[2]))

    def _search_replace_all(self, search: Search) -> None:
        if not search.glossary and not search.needle:
            return
        by_block: dict[Entry, list] = {}
        for entry, rec in self._fr_strings(search):
            by_block.setdefault(entry, []).append(rec.index)

        def planned():
            for entry, indices in by_block.items():
                edits = {}
                for index in indices:
                    rec = self._string(entry, index)
                    if rec is None:
                        continue
                    new, count = self._fr_replaced(search, rec.shown_text())
                    if count:
                        edits[index] = new
                yield entry, edits

        label = "Replace glossary terms" if search.glossary else "Replace all"
        n, refused = self._edit_blocks(planned(), label)
        where = {
            SELECTION: "the selection",
            BLOCK: "the block",
            PROJECT: "the project",
        }[search.scope]
        self._fr_at = None
        self.statusBar().showMessage(f"Replaced in {n} string(s) of {where}", 4000)
        if refused:
            self._report(
                "Not Replaced",
                f"{len(refused)} string(s) refused, and kept unwritten",
                refused,
            )

    # --- typed text, by its parts --------------------------------------------------

    @staticmethod
    def _fr_search(needle: str, replacement: str, case: bool, project: bool) -> Search:
        return Search(
            needle, replacement, case, PROJECT if project else BLOCK, skip_done=False
        )

    def _fr_find_next(self, needle: str, case: bool, project: bool = False) -> None:
        self._search_next(self._fr_search(needle, "", case, project))

    def _fr_replace_one(
        self, needle: str, replacement: str, case: bool, project: bool = False
    ) -> None:
        self._search_replace(self._fr_search(needle, replacement, case, project))

    def _fr_replace_all(
        self, needle: str, replacement: str, case: bool, project: bool = False
    ) -> None:
        self._search_replace_all(self._fr_search(needle, replacement, case, project))
