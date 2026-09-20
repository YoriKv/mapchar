"""Decompression previews, the structure scan, and the Compression picker."""

from __future__ import annotations

from dataclasses import replace

from mapchar.core.block import with_region
from mapchar.core.capabilities import Capability
from mapchar.core.document import Document
from mapchar.engines.scan import score_window
from mapchar.pipeline.scan import (
    decompress_at,
    find_next_structure,
    find_structures,
    scheme_at,
    signature_of,
)
from mapchar.plugins.base import Stage
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui import DUMP_WINDOW_BYTES
from mapchar.ui.raw_widget import RowModel
from mapchar.ui.widgets import fill_pick, select_data

AUTOMATIC = None
"""The Compression picker's first item: arm whichever registered scheme's
signature the view has landed on. The default, and what a file that never
picked anything is on."""

NO_SCHEME = ""
"""Its second: preview nothing, whatever the bytes look like."""


class CompressionMixin:
    """Decompression previews, the structure scan, and the Compression picker.

    The picker says what the Decompressed view reads a file through: a scheme by
    name, nothing, or — the default — whichever registered scheme announces
    itself where the view is. A pick is an instruction, so it holds wherever the
    view goes; automatic is the item that follows the bytes.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    # -- the picker -------------------------------------------------------------

    def _fill_compression_pick(self) -> None:
        """The schemes on offer: automatic, none, then every registered one."""
        fill_pick(
            self.compression_pick,
            [("automatic", AUTOMATIC), ("none", NO_SCHEME)]
            + [
                (p.info.name, p.info.id)
                for p in self.registry.plugins(Stage.COMPRESSION)
            ],
        )

    def _sync_compression_pick(self) -> None:
        """Show what the entry on screen reads its compression through.

        A block read through a scheme is already looking at the decompressed
        bytes, and what scheme that is was settled when it was made, so the
        picker states it and is disabled — changing it has a path of its own. A
        file's pick is its session's, and it is the one thing here that is a
        choice.
        """
        entry, pick = self._entry, self.compression_pick
        block = self._current_block()
        with self._bars_quiet():
            if block is not None:
                scheme = block.compression_id or NO_SCHEME
                if not select_data(pick, scheme):
                    # A scheme no plugin provides is still what the block reads
                    # through: said so, rather than shown as some other scheme.
                    pick.addItem(f"{scheme} (missing)", scheme)
                    pick.setCurrentIndex(pick.count() - 1)
                pick.setEnabled(False)
                return
            on_file = entry is not None and entry.kind is EntryKind.FILE
            pick.setEnabled(on_file)
            select_data(pick, entry.session.preview_scheme if on_file else AUTOMATIC)

    def _on_compression_pick(self) -> None:
        """A scheme picked on a file: arm the preview through it from here on.

        A view setting like the table beside it, so it is applied to the
        session and the views read again — no undo step, for the same reason a
        table pick on a file is none.
        """
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.FILE:
            return
        entry.session.preview_scheme = self.compression_pick.currentData()
        self._refresh_view(moved=True)

    # -- what is armed ----------------------------------------------------------

    def _preview_offset(self) -> int:
        """Where the preview reads from: the selection's first byte, or the
        view's own position when nothing is selected.

        The selection is what a click in the raw view leaves behind, so it is
        the closest thing to a cursor there is — and a structure is picked out
        by landing on its first byte.
        """
        return self._selection[0] if self._selection is not None else self._offset

    def _arm_scheme(self, scheme_id: str | None) -> None:
        """Arm ``scheme_id`` on the file on screen, picker and all.

        What Jump to Source and a bookmark made under a scheme do: the block
        they came from names the scheme its bytes are packed with, and a preview
        armed behind the picker's back would leave the two disagreeing.
        """
        self._preview_scheme = scheme_id
        entry = self._entry
        if entry is not None and entry.kind is EntryKind.FILE:
            entry.session.preview_scheme = scheme_id
        self._sync_compression_pick()
        # Shown at once rather than at the next refresh: what armed this may
        # land on the offset the view is already at, and move nothing.
        if self._doc is not None:
            self._refresh_decompress_preview(self._doc, self._table_set())

    def _armed_schemes(self) -> list:
        """The schemes automatic arming and Find All work over: the one picked,
        or, on automatic, every registered scheme that announces itself.

        A scheme with no signature cannot be found by looking, so automatic
        never considers one: it is picked by name or not at all.
        """
        pick = self._preview_pick()
        if pick is AUTOMATIC:
            return [
                p for p in self.registry.plugins(Stage.COMPRESSION) if signature_of(p)
            ]
        plugin = pick and self.registry.plugin(Stage.COMPRESSION, pick)
        return [plugin] if plugin else []

    def _preview_pick(self) -> str | None:
        """What the picker holds for the entry on screen. Only a file arms a
        preview, so anything else reads as none."""
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.FILE:
            return NO_SCHEME
        return entry.session.preview_scheme

    def _arm_automatically(self, doc: Document, offset: int) -> None:
        """On automatic, arm whichever scheme's signature sits at ``offset``.

        Run wherever the view lands, so it compares a few bytes per scheme
        before anything is decoded (:func:`~mapchar.pipeline.scan.scheme_at`),
        and only a complete structure counts — moving off one disarms, which is
        what hides the view again.
        """
        pick = self._preview_pick()
        if pick is not AUTOMATIC:
            self._preview_scheme = pick or None
            self._auto_armed = False
            return
        found = scheme_at(doc.data, self._armed_schemes(), offset)
        self._preview_scheme = found[0].info.id if found is not None else None
        self._auto_armed = found is not None

    # -- the preview ------------------------------------------------------------

    def _decompress_at(self, doc: Document, offset: int, *, partial: bool = True):
        """The picked scheme's reading of the structure at ``offset``, or ``None``.

        ``partial`` by default, because this is a *preview*: a scheme that can
        hand back the prefix it managed does, since the window the view is
        looking at is its own limit, not evidence that the data is bad.
        """
        plugin = self._scheme()
        if plugin is None:
            return None
        bind = getattr(plugin, "bind_tree", None)
        if callable(bind):
            try:
                bind(doc.data)
            except Exception:  # noqa: BLE001 - a scheme that cannot bind cannot read
                return None
        return decompress_at(doc.data, plugin, offset, partial=partial)

    def _complete_structure_at(self, doc: Document, offset: int):
        """The **complete** structure at ``offset``, read strictly, or ``None``.

        What the two actions that *act* on a structure work from, rather than
        the preview's partial read. A prefix's ``consumed`` is how far the
        window reached and not where the structure ends, so stepping by it
        would land mid-stream and recording it as a block's slot length would
        hand the write-back a boundary nobody measured. The same rule as the
        structure scan's, which is why a scheme with no end to find offers
        neither action.
        """
        found = self._decompress_at(doc, offset, partial=False)
        return found if found is not None and found.complete else None

    def _refresh_decompress_preview(self, doc: Document, tables) -> None:
        block = self._current_block()
        # A block that is *already* read through a scheme is looking at the
        # decompressed bytes, so there is nothing left to preview; anything else
        # is asked of the capability table rather than listed here.
        if not self._can(Capability.COMPRESSION_SCAN) or (
            block is not None and block.compression_id
        ):
            self.raw.set_structure(None)
            self.decompress_window.hide()
            return
        offset = self._preview_offset()
        self._arm_automatically(doc, offset)
        found = self._decompress_at(doc, offset)
        if found is None:
            self.raw.set_structure(None)
            self.decompress_window.hide()
            return
        data, consumed, complete = found.data, found.consumed, found.complete
        # Where the structure sits in the file shows in the file: only a
        # complete one, since a prefix's length is the window's, not its own.
        self.raw.set_structure((offset, offset + consumed) if complete else None)
        window = data[:DUMP_WINDOW_BYTES]
        tokens = self._decode_window(window, tables).tokens
        model = RowModel(0, window, tokens, set(), len(data))
        status = f"{consumed:,} compressed bytes at {offset:X} → {len(data):,} bytes"
        if self._auto_armed:
            # Nothing else says which scheme recognised the bytes: the picker
            # says only that it was left to them.
            status = f"{self._scheme().info.name}  ·  {status}"
        if not complete:
            status += "  ·  no end marker before the window's edge"
        self.decompress_window.show_result(model, tokens, status, complete)
        if not self.decompress_window.isVisible():
            self.decompress_window.show()

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
            self._go_to(offset + found.consumed)

    def _scan_next_structure(self) -> None:
        """Walk forward to the next complete structure, cancellably.

        The walk itself is :func:`~mapchar.pipeline.scan.find_next_structure`,
        which is Qt-free; all that is left here is reporting through the
        Decompressed view's own run/stop/progress line, which pumps the event
        loop so Stop stays clickable.
        """
        doc = self._doc
        plugin = self._scheme()
        if doc is None or plugin is None:
            return
        view = self.decompress_window
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
                result = find_next_structure(doc.data, plugin, start, on_tick=tick)
        finally:
            self._set_scan_ui(False)
        if result.found is not None:
            self.statusBar().showMessage(f"Structure at {result.found:X}", 5000)
            self._go_to(result.found)
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

        The walk is :func:`~mapchar.pipeline.scan.find_structures`, which is
        Qt-free and looks for a scheme's signature where it has one; what is
        left here is the same run/stop/progress line the structure scan uses,
        and scoring each payload for how text-like it reads under the current
        tables.
        """
        doc = self._doc
        schemes = self._armed_schemes()
        if doc is None or not schemes:
            self.decompress_window.set_structures(
                [], "Pick a scheme, or one that announces itself."
            )
            return
        view = self.decompress_window
        tables = self._table_set()
        total = max(doc.size, 1)

        def scored(data: bytes) -> float:
            return score_window(data, tables)[0]

        def tick(at: int) -> bool:
            return not view.progress(at, total)

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
            slice_offset=offset,
            # A decode that read nothing recorded nothing: the slot is left
            # unknown rather than claimed to be empty, and the write-back then
            # bounds it by the end of the parent's buffer.
            slice_length=consumed or None,
        )
        self._push_add(entry)
        self._activate_entry(entry)
