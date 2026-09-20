"""Walking a whole file for compressed structures, and acting on one found."""

from __future__ import annotations

from dataclasses import replace

from mapchar.core.block import with_region
from mapchar.engines.textscan import score_window
from mapchar.pipeline.structures import find_next_structure, find_structures
from mapchar.project.workspace import Entry, EntryKind

NOTHING_TO_WALK = "Pick a scheme, or one that announces itself."
"""What Scan and Find All say with no scheme to walk the file for at all."""


class StructureScanMixin:
    """Walking a whole file for compressed structures, and acting on one found.

    A different lifetime from the preview beside it
    (:mod:`mapchar.ui.main_window.compression`): a walk owns the view for as
    long as it runs, freezes the rest of the window, and is stopped rather than
    refreshed.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _go_to_structure(self, offset: int) -> None:
        """A row of the structure list: show the file there, armed on it.

        Through the selection, as every jump this feature makes is: the preview
        reads from the selection's first byte, so a jump that moved the view
        alone would leave it reading the byte last clicked.
        """
        self._select_bytes(offset, 1)

    def _jump_next_structure(self) -> None:
        if self._doc is None:
            return
        offset = self._preview_offset()
        found = self._complete_structure_at(self._doc, offset)
        if found is None:
            self.statusBar().showMessage(
                "No complete structure here to step past", 4000
            )
            return
        if found.consumed > 0:
            self._go_to_structure(offset + found.consumed)

    def _scan_next_structure(self) -> None:
        """Walk forward to the next complete structure, cancellably.

        The walk itself is :func:`~mapchar.pipeline.structures.find_next_structure`,
        which is Qt-free; all that is left here is reporting through the
        Decompressed view's own run/stop/progress line, which pumps the event
        loop so Stop stays clickable — and so a click on a button that pumping
        delivers can reach this again, which the flag refuses.

        Over the same schemes Find All covers: the one picked, or every scheme
        that announces itself on automatic. Automatic is the default, and
        nothing is armed where a search for the first structure starts, so a
        scan of the armed scheme alone would be a button that does nothing.
        """
        doc = self._doc
        schemes = self._armed_schemes()
        if doc is None or self._scanning:
            return
        view = self.decompress_window
        if not schemes:
            view.status.setText(NOTHING_TO_WALK)
            return
        start = self._preview_offset() + 1
        total = max(doc.size - start, 1)

        def tick(at: int) -> bool:
            # The scan stops when a tick answers True; the run's progress
            # answers False for the same thing.
            return not view.progress(at - start, total)

        # The scan runs inline; everything else is frozen for its duration, since
        # a window whose offset is about to move cannot answer for anything asked
        # of it meanwhile.
        self._set_scan_ui(True)
        try:
            with view.running():
                result = find_next_structure(doc.data, schemes, start, on_tick=tick)
        finally:
            self._set_scan_ui(False)
        if result.found is not None:
            self.statusBar().showMessage(f"Structure at {result.found:X}", 5000)
            self._go_to_structure(result.found)
        elif result.stopped:
            self.statusBar().showMessage(f"Scan stopped at {result.end:X}", 5000)
        else:
            self.statusBar().showMessage("No further structure found", 5000)

    def _set_scan_ui(self, active: bool) -> None:
        """Freeze the window for the length of a structure scan, and thaw it.

        The flag as well as the widgets: a scan pumps the event loop, so keys
        routed through the application filter reach the window even while its
        central widget is disabled, and the one thing a scan owns outright is the
        view position.
        """
        self._scanning = active
        self.decompress_window.set_scanning(active)
        for widget in (
            self.menuBar(),
            self.centralWidget(),
            self.files_dock,
            self.hex_dock,
        ):
            widget.setEnabled(not active)

    # -- the whole file ---------------------------------------------------------

    def _find_all_structures(self) -> None:
        """Find All: every structure in the file, listed, cancellably.

        The walk is :func:`~mapchar.pipeline.structures.find_structures`, which is
        Qt-free and looks for a scheme's signature where it has one; what is
        left here is the same run/stop/progress line the structure scan uses,
        and scoring each payload for how text-like it reads under the current
        tables. Re-entry is refused for the reason
        :meth:`_scan_next_structure` refuses it: one walk owns the view.
        """
        doc = self._doc
        schemes = self._armed_schemes()
        if self._scanning:
            return
        if doc is None or not schemes:
            self.decompress_window.set_structures([], NOTHING_TO_WALK)
            return
        view = self.decompress_window
        tables = self._table_set()
        # The walk restarts at 0 for each scheme, so the line counts every
        # scheme's pass: one run from 0 to 100%, however many schemes it takes.
        total = max(doc.size * len(schemes), 1)
        walked = 0
        last = 0

        def scored(data: bytes) -> float:
            return score_window(data, tables)[0]

        def tick(at: int) -> bool:
            nonlocal walked, last
            if at < last:  # a position that went backwards is the next scheme
                walked += doc.size
            last = at
            return not view.progress(walked + at, total)

        self._set_scan_ui(True)
        try:
            with view.running_as(view.find_button, "Searching"):
                result = find_structures(
                    doc.data,
                    schemes,
                    score=None if tables is None else scored,
                    on_tick=tick,
                )
        finally:
            self._set_scan_ui(False)
        note = f"{len(result.found)} structure(s)" + (
            " (stopped)" if result.stopped else ""
        )
        view.set_structures(result.found, note)
        self._structures_file = self._current_file()
        # The buttons the freeze switched off are armed again by what the
        # position justifies, which is the ordinary refresh's answer.
        self._refresh_view(moved=True)

    def _forget_structures(self) -> None:
        """Drop the structure list when another file comes on screen: it is one
        file's offsets, and they mean nothing in the next."""
        file_entry = self._current_file()
        if file_entry is not self._structures_file:
            self._structures_file = None
            self.decompress_window.clear_structures()

    # -- acting on a structure --------------------------------------------------

    def _structure_to_block(self) -> None:
        file_entry = self._current_file()
        doc = self._doc
        if file_entry is None or doc is None:
            return
        offset = self._preview_offset()
        found = self._complete_structure_at(doc, offset)
        if found is None:
            self._error(
                "The scheme does not read a complete structure here, so there "
                "is no region to make a block of. A partial decode's length is "
                "the window's, not the structure's."
            )
            return
        data, consumed = found.data, found.consumed
        cfg = with_region(self._reading() or self._default_reading(), 0, len(data))
        if not cfg.table_id:
            cfg = replace(cfg, table_id=self._default_table_id())
        entry = Entry(
            EntryKind.BLOCK,
            f"Compressed {offset:X}",
            file_entry.path,
            parent=file_entry,
            config=cfg,
            # Whatever actually decoded, which under automatic arming is the
            # scheme the bytes announced rather than anything anyone picked.
            compression_id=self._preview_scheme,
            slot_offset=offset,
            # A decode that read nothing recorded nothing: the slot is left
            # unknown rather than claimed to be empty, and the write-back then
            # bounds it by the end of the parent's buffer.
            slot_length=consumed or None,
        )
        self._push_add(entry)
        self._activate_entry(entry)
