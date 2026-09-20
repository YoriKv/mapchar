"""Find and Replace over the strings' text."""

from __future__ import annotations

from mapchar.engines import scriptfind
from mapchar.project.entry import Entry


class FindReplaceMixin:
    """Find and Replace over the strings' text.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_find_replace(self) -> None:
        self.find_replace.show()
        self.find_replace.raise_()
        self.find_replace.find.setFocus()

    def _fr_blocks(self, project: bool) -> list[Entry]:
        """The blocks a search runs over, the current one first.

        In project scope every block is read first, so a block the user has
        not opened yet is searched too.
        """
        current = self._current_block(need_doc=True)
        if not project:
            return [current] if current is not None else []
        blocks = [e for e, doc in self._readable_blocks() if doc.strings]
        if current in blocks:
            blocks.remove(current)
            blocks.insert(0, current)
        return blocks

    def _fr_order(self, blocks: list[Entry]) -> list[tuple[Entry, object]]:
        """Every ``(block, string)`` to visit, starting after the selected one."""
        pairs = [(e, r) for e in blocks for r in e.doc.strings]
        selected = self.strings.selected_indices()
        at = 0
        if selected and blocks:
            at = next(
                (
                    i + 1
                    for i, (e, r) in enumerate(pairs)
                    if e is self._entry and r.index == selected[0]
                ),
                0,
            )
        return pairs[at:] + pairs[:at]

    def _fr_find_next(self, needle: str, case: bool, project: bool = False) -> None:
        blocks = self._fr_blocks(project)
        if not blocks or not needle:
            return
        for entry, rec in self._fr_order(blocks):
            if scriptfind.contains(rec.current_text(), needle, case=case):
                if entry is not self._entry:
                    self._activate_entry(entry)
                self.strings.select_index(rec.index)
                self._on_string_row(rec.index)
                return
        self.statusBar().showMessage("Not found", 3000)

    def _fr_replace_one(
        self, needle: str, replacement: str, case: bool, project: bool = False
    ) -> None:
        entry = self._current_block(need_doc=True)
        selected = self.strings.selected_indices()
        if not selected or not needle:
            self._fr_find_next(needle, case, project)
            return
        rec = self._string(entry, selected[0])
        if rec is not None:
            new, count = scriptfind.replace(
                rec.current_text(), needle, replacement, case=case
            )
            if count:
                problems = self._set_translation(entry, rec.index, new)
                if problems:
                    self._refuse_edit(problems)
                    return
        self._fr_find_next(needle, case, project)

    def _fr_replace_all(
        self, needle: str, replacement: str, case: bool, project: bool = False
    ) -> None:
        blocks = self._fr_blocks(project)
        if not blocks or not needle:
            return

        def planned():
            for entry in blocks:
                edits = {}
                for rec in list(entry.doc.strings):
                    new, count = scriptfind.replace(
                        rec.current_text(), needle, replacement, case=case
                    )
                    if count:
                        edits[rec.index] = new
                yield entry, edits

        n, refused = self._edit_blocks(planned(), "Replace all")
        where = "the project" if project else "the block"
        self.statusBar().showMessage(f"Replaced in {n} string(s) of {where}", 4000)
        if refused:
            self._report("Not Replaced", f"{len(refused)} string(s) refused", refused)
