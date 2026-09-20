"""Pointer discovery over the current file.

The search is every mapping crossed with every pointer width, endianness and
offset, each combination scanned over the whole file — long enough on a large ROM
with a hundred strings that it needs a way out, like every other long operation
here. :class:`~mapchar.ui.progress.ModalProgress` supplies the progress bar and
the Stop button, and the engine takes them as its ``progress`` callback; a
stopped search still offers the combinations it had already ranked.

What the search covers is asked first, by
:class:`~mapchar.ui.dialogs.PointerSearchDialog`: scope and the offset range are
both the candidate space rather than the ranking, so neither can be settled
afterwards. What is done with the result is asked last: **Use as pointer table**
rewrites the block's source, **Attach** leaves it alone and puts the found
pointers on the strings.
"""

from __future__ import annotations

from mapchar.engines.pointers import discover
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import resolve_mapping
from mapchar.project.workspace import Entry
from mapchar.ui.dialogs import DiscoveryDialog, PointerSearchDialog
from mapchar.ui.progress import ModalProgress
from mapchar.ui.undo_commands import PointerCommand


class PointerDiscoveryMixin:
    """Pointer discovery over the current file.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    _pointer_progress = None
    """The running search's progress bar, while one is running."""

    def _find_pointers(self) -> None:
        entry = self._current_block(need_doc=True, complain="Select a block first.")
        if entry is None:
            return
        if not entry.doc.strings:
            self._error("The block has no strings.")
            return
        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        setup = PointerSearchDialog(
            len(entry.doc.strings), rec.index if rec is not None else None, self
        )
        if setup.exec() != PointerSearchDialog.DialogCode.Accepted:
            return
        starts = (
            [rec.start]
            if setup.selected_only() and rec is not None
            else [r.start for r in entry.doc.strings]
        )
        mappings = {
            mid: resolve_mapping(self.registry, mid)
            for mid in self.registry.ids(Stage.MAPPING)
        }
        with ModalProgress(
            self,
            "Find Pointers",
            f"Looking for pointers to {len(starts)} string(s)…",
        ) as run:
            # Held on the window while it runs, the way the structure scan holds
            # its stop flag: the search is the only thing happening, and anything
            # that wants to end it early has one place to say so.
            self._pointer_progress = run
            try:
                candidates = discover(
                    entry.doc.data,
                    starts,
                    mappings,
                    offsets=setup.offsets(),
                    # The block's own bank, for a mapping that needs one and
                    # cannot work out an offset's for itself: the block is
                    # already read in it, so it is the better guess than zero.
                    bank=getattr(entry.config.source, "bank", 0),
                    progress=run.progress,
                )
            finally:
                self._pointer_progress = None
            stopped = run.cancelled
        if not candidates:
            self._error(
                "The search was stopped before it found anything."
                if stopped
                else "No pointers to these strings were found."
            )
            return
        dialog = DiscoveryDialog(candidates, self)
        if dialog.exec() != DiscoveryDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen()
        if chosen is None:
            return
        if dialog.attach:
            self._attach_pointers(entry, chosen)
        else:
            self._use_as_pointer_table(entry, chosen)

    def _use_as_pointer_table(self, entry: Entry, candidate) -> None:
        """Read the block from the found table: a block edit like any made in
        the bars, so it re-reads with the translations kept and undoes."""
        from dataclasses import replace

        self._push_block_edit(
            entry, config=replace(entry.config, source=candidate.source())
        )

    def _attach_pointers(self, entry: Entry, candidate) -> None:
        """Put the found pointers on the strings they reach, source untouched.

        The block goes on reading its strings from wherever it read them before,
        so a block whose pointers are scattered rather than tabulated gains the
        addresses that reach it without having to pretend they are a table.
        """
        refs = candidate.refs()
        before: dict[int, tuple] = {}
        after: dict[int, tuple] = {}
        for rec in entry.doc.strings:
            found = refs.get(rec.start)
            if found is None or found == rec.pointers:
                continue
            before[rec.index] = rec.pointers
            after[rec.index] = found
        if not after:
            self._error("Those strings already carry the pointers that were found.")
            return
        self._push_command(PointerCommand(self, entry, before, after))

    def apply_pointers(self, entry: Entry, pointers: dict[int, tuple]) -> None:
        """Give the named strings these pointers, by index.

        No revision token, unlike every other command over records: the pointers
        live on the document, which the project file does not carry, so an attach
        is not an unsaved edit to the entry. Marking it dirty would promise a save
        that cannot keep it.
        """
        if entry.doc is not None:
            for rec in entry.doc.strings:
                if rec.index in pointers:
                    rec.pointers = pointers[rec.index]
        self.files_panel.refresh_labels()
        self._refresh_view()
