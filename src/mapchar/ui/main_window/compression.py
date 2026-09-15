"""Decompression previews and the structure scan."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QApplication

from mapchar.core.block import with_region
from mapchar.core.capabilities import Capability
from mapchar.core.document import Document
from mapchar.pipeline.pipeline import decompress_at, find_next_structure
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui import DUMP_WINDOW_BYTES
from mapchar.ui.raw_widget import RowModel


class CompressionMixin:
    """Decompression previews and the structure scan.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

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
        entry = self._entry
        # A block that is *already* read through a scheme is looking at the
        # decompressed bytes, so there is nothing left to preview; anything else
        # is asked of the capability table rather than listed here.
        if not self._can(Capability.COMPRESSION_SCAN) or (
            entry is not None and entry.kind is EntryKind.BLOCK and entry.compression_id
        ):
            self.decompress_window.hide()
            return
        found = self._decompress_at(doc, self._offset)
        if found is None:
            self.decompress_window.hide()
            return
        data, consumed, complete = found.data, found.consumed, found.complete
        window = data[:DUMP_WINDOW_BYTES]
        tokens = self._decode_window(window, tables).tokens
        model = RowModel(0, window, tokens, set(), len(data))
        status = (
            f"{consumed:,} compressed bytes at {self._offset:X} → {len(data):,} bytes"
        )
        if not complete:
            status += "  ·  no end marker before the window's edge"
        self.decompress_window.show_result(model, status, complete)
        if not self.decompress_window.isVisible():
            self.decompress_window.show()

    def _jump_next_structure(self) -> None:
        if self._doc is None:
            return
        found = self._complete_structure_at(self._doc, self._offset)
        if found is None:
            self.statusBar().showMessage(
                "No complete structure here to step past", 4000
            )
            return
        if found.consumed > 0:
            self._go_to(self._offset + found.consumed)

    def request_scan_stop(self) -> None:
        """Abandon a running structure scan. What the Stop button calls."""
        self._scan_stop = True

    def _scan_next_structure(self) -> None:
        """Walk forward to the next complete structure, cancellably.

        The walk itself is :func:`~mapchar.pipeline.pipeline.find_next_structure`,
        which is Qt-free; all that is left here is pumping the event loop so the
        Stop button can be clicked, and answering from what the user did.
        """
        doc = self._doc
        plugin = self._scheme()
        if doc is None or plugin is None:
            return
        self._scan_stop = False

        def tick(at: int) -> bool:
            self.statusBar().showMessage(f"Scanning… {at:X}")
            QApplication.processEvents()
            return self._scan_stop

        # The scan runs inline, pumping the event loop so Stop stays clickable;
        # everything else is frozen for its duration, since a window whose offset
        # is about to move cannot answer for anything asked of it meanwhile.
        self._set_scan_ui(True)
        try:
            result = find_next_structure(
                doc.data, plugin, self._offset + 1, on_tick=tick
            )
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
            self.tables_dock,
            self.fonts_dock,
            self.hex_dock,
        ):
            widget.setEnabled(not active)

    def _structure_to_block(self) -> None:
        file_entry = self._current_file()
        doc = self._doc
        if file_entry is None or doc is None:
            return
        found = self._complete_structure_at(doc, self._offset)
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
            f"Compressed {self._offset:X}",
            file_entry.path,
            parent=file_entry,
            config=cfg,
            compression_id=self._preview_scheme,
            slice_offset=self._offset,
            # A decode that read nothing recorded nothing: the slot is left
            # unknown rather than claimed to be empty, and the write-back then
            # bounds it by the end of the parent's buffer.
            slice_length=consumed or None,
        )
        self._push_add(entry)
        self._activate_entry(entry)
