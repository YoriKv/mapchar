"""The Strings grid's commands: its context menu, the marks, the steps."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QMenu

from mapchar.core.block import Align, ChainMode, Status
from mapchar.core.errors import EncodeError
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, repair_chains
from mapchar.project.entry import Entry
from mapchar.ui.find_replace import BLOCK, PROJECT
from mapchar.ui.main_window.string_rows import same_key
from mapchar.ui.strings_view import FLAGGED
from mapchar.ui.undo_commands import StringFieldCommand, StringsEditCommand

ALIGN_NAMES = {
    None: "Block's",
    Align.LEFT: "Left",
    Align.CENTRE: "Centre",
    Align.RIGHT: "Right",
}
"""The Align menu's rows: a string's own alignment, or none to take the
block's."""


class StringsMenuMixin:
    """The Strings grid's commands: its context menu, the marks, the steps.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _revert_selected(self) -> None:
        """Put the original text back into the bytes of the selected strings,
        and let go of any translation they keep unwritten."""
        entry = self._entry
        selected = self.strings.selected_indices()
        edits = {}
        for index in selected:
            rec = self._string(entry, index)
            if rec is not None and rec.edited:
                edits[index] = rec.original
        with self._macro("Revert to original"):
            problems = (
                self._edit_strings(entry, edits, "Revert to original") if edits else []
            )
            if not problems:
                for index in selected:
                    self._keep_unwritten(entry, index, None)
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
        text = rec.shown_text()
        found = self._identical_originals(rec, entry, project)

        def planned():
            for block, indices in found.items():
                yield (
                    block,
                    {
                        i: text
                        for i in indices
                        if (r := self._string(block, i)) is not None
                        and r.shown_text() != text
                    },
                )

        n, problems = self._edit_blocks(planned(), "Apply to identical originals")
        self.strings.select_index(index)
        self.statusBar().showMessage(f"Applied to {n} string(s)", 4000)
        if problems:
            self._report("Not Applied", f"{len(problems)} string(s) refused", problems)

    def _toggle_chain_break(self, indices: list[int]) -> None:
        """Make the selected strings each begin a chain, or — when the first of
        them does — tie them to the string before again: a block edit, since
        where the chains break is part of how the block is read."""
        entry = self._current_block(need_doc=True)
        if entry is None or not entry.config.chained:
            return
        cfg = entry.config
        breaks = set(cfg.chain_breaks)
        chosen = {i for i in indices if i > 0}
        if not chosen:
            return
        first = min(chosen)
        breaks = breaks - chosen if first in breaks else breaks | chosen
        self._push_block_edit(
            entry, config=replace(cfg, chain_breaks=tuple(sorted(breaks)))
        )

    def _align_selected(self, indices: list[int], align: Align | None) -> None:
        """Where the padded text of each selected string sits, as one step;
        ``None`` hands it back to the block's alignment. The bytes follow at
        the string's next edit."""
        entry = self._entry
        with self._macro("Align strings"):
            for index in indices:
                rec = self._string(entry, index)
                if rec is None or rec.align is align:
                    continue
                self._push_command(
                    StringFieldCommand(
                        self,
                        entry,
                        index,
                        "align",
                        rec.align.value if rec.align else None,
                        align.value if align else None,
                    )
                )

    def _repair_chains(self) -> None:
        """Close every gap in the current block's chains, as one undo step: each
        string shortened outside its chain is padded over the fill after it
        (:func:`~mapchar.pipeline.insert.repair_chains`)."""
        entry = self._current_block(need_doc=True, complain="Select a block first.")
        if entry is None:
            return
        doc, cfg = entry.doc, entry.config
        if not cfg.chained or not doc.chain_gaps:
            self.statusBar().showMessage("No chain of this block starts in fill", 4000)
            return
        tables = self._table_set_of(entry)
        if tables is None:
            self._error(f"Table @{cfg.table_id} is not loaded.")
            return
        try:
            splices, problems = repair_chains(
                doc.data, cfg, tables, doc.strings, doc.chain_gaps
            )
        except EncodeError as exc:
            self._error(f"The chains cannot be repaired: {exc}")
            return
        if problems:
            self._report(
                "Chains Not Repaired",
                f"{len(problems)} gap(s) cannot be closed",
                [f"#{p.index}: {p.message}" for p in problems],
            )
            return
        new = apply_splices(doc.data, splices)
        after = extract(new, cfg, tables, self.registry)
        if after.chain_gaps or len(after.strings) != len(doc.strings):
            self._error("The repair would not read back as the same strings.")
            return
        lo = min(s.offset for s in splices)
        hi = max(s.end for s in splices)
        self._push_command(
            StringsEditCommand(
                self,
                entry,
                lo,
                doc.data[lo:hi],
                new[lo:hi],
                min(doc.chain_gaps) - 1,
                "Repair chains",
            )
        )
        self.statusBar().showMessage(f"Repaired {len(doc.chain_gaps)} gap(s)", 4000)

    def _chain_menu(self, menu: QMenu, indices: list[int]) -> None:
        """A chained block's rows: where its chains break, where each padded
        text sits, and the repair of a chain a shorter string broke."""
        cfg = self._entry.config if self._entry is not None else None
        if cfg is None or not cfg.chained or not indices:
            return
        menu.addSeparator()
        rec = self._string(self._entry, indices[0])
        brk = menu.addAction("Chain &Break", lambda: self._toggle_chain_break(indices))
        brk.setCheckable(True)
        brk.setChecked(indices[0] in cfg.chain_breaks)
        brk.setEnabled(any(i > 0 for i in indices))
        brk.setToolTip("The string begins a chain of its own")
        align = menu.addMenu("A&lign")
        align.setEnabled(cfg.chain is ChainMode.PAD)
        group = QActionGroup(align)
        for value, name in ALIGN_NAMES.items():
            label = f"{name} ({cfg.align.value})" if value is None else name
            a = align.addAction(label, lambda v=value: self._align_selected(indices, v))
            a.setCheckable(True)
            a.setChecked(rec is not None and rec.align is value)
            group.addAction(a)
        menu.addAction("Repair C&hains", self._repair_chains).setEnabled(
            bool(self._entry.doc.chain_gaps)
        )

    def _strings_menu(self, indices: list[int], pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Re&vert to Original", self._revert_selected)
        menu.addAction("Toggle Revie&w", self._toggle_review_selected)
        menu.addAction("Toggle &Done", self._toggle_done_selected)
        kept = [
            i
            for i in indices
            if (r := self._string(self._entry, i)) is not None
            and r.unwritten is not None
        ]
        menu.addAction(
            "Write &Unwritten Translation", self._write_unwritten_selected
        ).setEnabled(bool(kept))
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
        self._chain_menu(menu, indices)
        menu.addSeparator()
        terms = any(t.translation for t in self.workspace.glossary)
        for text, slot in (
            ("Replace &Glossary Terms…", self._replace_terms_in_selection),
            (
                "Replace Glossary Terms in &Block…",
                lambda: self._replace_glossary_terms(scope=BLOCK),
            ),
            (
                "Replace Glossary Terms in Pro&ject…",
                lambda: self._replace_glossary_terms(scope=PROJECT),
            ),
        ):
            menu.addAction(text, slot).setEnabled(terms)
        menu.exec(pos)

    def _step_strings(self, what: str, backwards: bool = False) -> None:
        """Edit ▸ Next / Previous Untranslated or Flagged: the next row of that
        kind among the rows the filter shows, wrapping round."""
        if self._current_block(need_doc=True) is None:
            return
        if what == "untranslated":
            # The record's own status, not the row's: a row shows "unwritten"
            # in place of it, and an untouched string is still untranslated.
            wanted = lambda d: self._is_untouched(d.index)  # noqa: E731
        else:
            wanted = lambda d: d.status in FLAGGED or bool(d.misses)  # noqa: E731
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
        self.raw.select_bytes(rec.start, rec.end)
        self._on_selection(rec.start, rec.end)
