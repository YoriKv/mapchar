"""The Strings grid's commands: its context menu, the marks, the steps."""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from mapchar.core.block import Status
from mapchar.project.workspace import Entry
from mapchar.ui.main_window.string_rows import same_key
from mapchar.ui.strings_view import FLAGGED
from mapchar.ui.undo_commands import StringFieldCommand


class StringsMenuMixin:
    """The Strings grid's commands: its context menu, the marks, the steps.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _revert_selected(self) -> None:
        """Put the original text back into the bytes of the selected strings."""
        entry = self._entry
        edits = {}
        for index in self.strings.selected_indices():
            rec = self._string(entry, index)
            if rec is not None and rec.edited:
                edits[index] = rec.original
        if not edits:
            return
        problems = self._edit_strings(entry, edits, "Revert to original")
        if problems:
            self._refuse_edit(problems)

    def _toggle_review_selected(self) -> None:
        self._toggle_status_selected(Status.REVIEW)

    def _toggle_done_selected(self) -> None:
        self._toggle_status_selected(Status.DONE)

    def _toggle_status_selected(self, held: Status) -> None:
        """Mark the selected strings ``held`` — review or done — or, when they
        are, let the bytes settle their status again."""
        with self._macro(f"Toggle {held.value}"):
            for index in self.strings.selected_indices():
                rec = self._string(self._entry, index)
                if rec is None:
                    continue
                new = Status.EDITED if rec.status is held else held
                self._push_command(
                    StringFieldCommand(
                        self, self._entry, index, "status", rec.status.value, new.value
                    )
                )

    def _identical_originals(self, rec, entry, project: bool) -> dict[Entry, list[int]]:
        """Every other string whose original is ``rec``'s, by block: the block's
        own, or every block of the project that is read."""
        key = same_key(rec.original)
        blocks = [entry]
        if project:
            blocks += [b for b, _ in self._readable_blocks(besides=entry)]
        found: dict[Entry, list[int]] = {}
        for block in blocks:
            if block.doc is None:
                continue
            hits = [
                r.index
                for r in block.doc.strings
                if same_key(r.original) == key
                and not (block is entry and r.index == rec.index)
            ]
            if hits:
                found[block] = hits
        return found

    def _apply_to_identical(self, index: int, project: bool) -> None:
        """The selected string's text into every string with the same original."""
        entry = self._entry
        rec = self._string(entry, index)
        if rec is None:
            return
        text = rec.current_text()
        found = self._identical_originals(rec, entry, project)

        def planned():
            for block, indices in found.items():
                yield (
                    block,
                    {
                        i: text
                        for i in indices
                        if (r := self._string(block, i)) is not None
                        and r.current_text() != text
                    },
                )

        n, problems = self._edit_blocks(planned(), "Apply to identical originals")
        self.strings.select_index(index)
        self.statusBar().showMessage(f"Applied to {n} string(s)", 4000)
        if problems:
            self._report("Not Applied", f"{len(problems)} string(s) refused", problems)

    def _strings_menu(self, indices: list[int], pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Re&vert to Original", self._revert_selected)
        menu.addAction("Toggle Revie&w", self._toggle_review_selected)
        menu.addAction("Toggle &Done", self._toggle_done_selected)
        if indices:
            rec = self._string(self._entry, indices[0])
            if rec is not None:
                menu.addAction(
                    "Copy &Original",
                    lambda: QApplication.clipboard().setText(rec.original),
                )
                menu.addSeparator()
                index = indices[0]
                same = menu.addAction(
                    "&Apply to Identical Originals in Block",
                    lambda: self._apply_to_identical(index, False),
                )
                same.setEnabled(
                    bool(self._identical_originals(rec, self._entry, False))
                )
                menu.addAction(
                    "Apply to Identical Originals in &Project",
                    lambda: self._apply_to_identical(index, True),
                )
        menu.exec(pos)

    def _step_strings(self, what: str, backwards: bool = False) -> None:
        """Edit ▸ Next / Previous Untranslated or Flagged: the next row of that
        kind among the rows the filter shows, wrapping round."""
        if self._current_block(need_doc=True) is None:
            return
        if what == "untranslated":
            # The record's own status, not the row's: a row shows "overflows
            # box" in place of it, and an untouched string is still untranslated
            # whatever its box says about it.
            wanted = lambda d: self._is_untouched(d.index)  # noqa: E731
        else:
            wanted = lambda d: d.status in FLAGGED  # noqa: E731
        self._show_view("strings")
        if not self.strings.step_to(wanted, backwards):
            self.statusBar().showMessage(f"No {what} string", 3000)

    def _is_untouched(self, index: int) -> bool:
        """Whether the current block's string at ``index`` is still untouched."""
        rec = self._string(self._entry, index)
        return rec is not None and rec.status is Status.UNTOUCHED

    def _on_string_row(self, index: int) -> None:
        rec = self._string(self._entry, index)
        if rec is None:
            return
        # Moving off a row ends the editing run on it, so the next edit starts a
        # step of its own rather than merging into the last one
        # (:class:`~mapchar.ui.undo_commands.StringFieldCommand`).
        if not self._applying_undo:
            self._edit_run += 1
        self._sync_preview()
        self._sync_glossary()
        if not (self._offset <= rec.start < self._offset + self._view_bytes()):
            self._go_to(rec.start)
        # Through the one selection path, so the Hex dock follows too — and
        # after the move, which would otherwise sync it before the selection is
        # on the window. ``select_index`` blocks its signals, so the row the
        # selection lands back on does not come round again.
        self.raw.set_selection(rec.start, rec.end)
        self._on_selection(rec.start, rec.end)
