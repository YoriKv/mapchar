"""The refresh cycle: the single choke point after any change."""

from __future__ import annotations

from dataclasses import replace

from mapchar.core.bits import Bits
from mapchar.core.block import NestedPointerSource, block_bound
from mapchar.core.document import Document
from mapchar.core.table import TableSet
from mapchar.engines.decode import RunResult
from mapchar.pipeline.view_read import (
    PointerCell,
    decode_strings,
    pointer_cells,
    target_string,
)
from mapchar.project.progress import progress_text
from mapchar.ui import BYTES_PER_ROW
from mapchar.ui.pointer_tokens import (
    hex_tokens,
    preview_reader,
    view_source,
)
from mapchar.ui.raw_cells import RowModel

VIEWS = ("raw", "text", "strings")
"""A session's name for each central tab, in tab order: Hex, Text, Strings."""

DRAG_REST_MS = 120
"""How long after the last move of a dragged view the refresh it put off runs.
Long enough that a drag never pays for it, short enough that letting go and
reading the panels feels immediate."""


class RefreshMixin:
    """The refresh cycle: the single choke point after any change.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _refresh_view(self, *, moved: bool = False, live: bool = False) -> None:
        """Re-render everything from the document as it stands.

        ``moved`` says only the view changed — its offset, its bounds, whether
        its pointers resolve — so the Strings grid, which shows the same strings
        wherever the view is, is left as it is unless the move re-read them.
        Filling it is the one part of a refresh that costs by the string, and a
        move must cost nothing.

        ``live`` says the view is being dragged, so another move is already on
        its way: only the tab on screen is rendered, and everything that merely
        reads where the view sits — the other tab, the side panels, the title's
        unsaved marker — waits for the drag to come to rest
        (:meth:`_on_drag_rest`). What the drag shows is what it costs.
        """
        doc, entry = self._doc, self._entry
        block = self._current_block()
        if doc is None:
            self._bounds = None
            self._text_decode = None
            self.raw.set_model(None)
            self.strings.set_rows([])
            self.nav_status.setText("")
            self.block_label.setText("")
            self.raw.set_structure(None)
            self.offset_box.set_value(None)
            self._update_title()
            self._sync_capabilities()
            self._sync_steps()
            return
        total = doc.size
        self._offset = max(0, min(self._offset, max(total - 1, 0)))
        # A position outside the view's bounds widens them to the whole file:
        # whatever asked for it — a typed address, a Search Window hit, an undo
        # reaching its edit — meant to be shown there, not clamped away from it.
        if self._bounds is not None and not (
            self._bounds[0] <= self._offset < self._bounds[1]
        ):
            self._bounds = None
        self.offset_box.set_value(self._offset)
        if not moved:
            self._sync_bars()
        tables = self._table_set()
        if not moved:
            self._text_decode = None
        if live:
            self._drag_rest.start()
            if self.tabs.currentWidget() is self.text:
                self._refresh_text_mode(doc, tables)
            else:
                self._refresh_raw(doc, tables)
            self._update_nav_status()
            return
        reread = block is not None and self._extract_current(entry, doc, tables)
        # A block whose configuration never made it back — a project file that
        # held none — is still a block; it just has no reading to spell out.
        configured = self._current_block(need_config=True)
        if configured is not None and isinstance(
            configured.config.source, NestedPointerSource
        ):
            self.reading_bar.show_bound_default("each group's end")
        else:
            self.reading_bar.show_bound_default(
                block_bound(
                    replace(configured.config, bound=None),
                    doc.strings,
                    configured.room,
                )
                if configured is not None
                else None
            )
        self._refresh_raw(doc, tables)
        self._refresh_text_mode(doc, tables)
        self._refresh_decompress_preview(doc, tables)
        if block is not None and (reread or not moved):
            self._fill_strings(doc)
            self.block_label.setText(
                f"{self._block_label(block, len(doc.strings))} · "
                f"{progress_text(self.workspace, doc)}"
            )
        elif block is None:
            # The bar keeps its row on a file, and says which file it is.
            self.block_label.setText(f"{entry.name} · {doc.size:,} bytes")
        self._update_nav_status()
        self.search_window.set_data(doc.data)
        self.scan_window.set_source(doc.data, tables)
        self._sync_hex_panel()
        self._sync_preview()
        self._sync_glossary()
        self._update_title()
        # Last, so the capability table has the final word: every pass above
        # enables controls on grounds that are true in general and beside the
        # point for an entry of the wrong kind.
        self._sync_capabilities()
        self._sync_steps()

    def _on_drag_rest(self) -> None:
        """The dragged view has come to rest: run the refresh its moves put off.

        Reached by the timer and by letting go of the scrollbar, so a drag that
        ends on a move the timer has not yet reached still catches up at once.
        Harmless with nothing outstanding — it is the ordinary refresh of a view
        that has not moved.

        A drag that pauses with the handle still held has not come to rest: the
        timer waits again rather than making the user pay for the whole refresh
        in the middle of the gesture. It keeps running, so a handle that comes
        up without a release ever reaching here is caught by the next round.
        """
        if self._dragging():
            self._drag_rest.start()
            return
        self._drag_rest.stop()
        if self._doc is not None:
            self._refresh_view(moved=True)

    def _refresh_raw(self, doc: Document, tables: TableSet | None) -> None:
        """The raw view's window: as many rows as it shows, and one more for
        the row cut off at its bottom edge."""
        block = self._current_block()
        window = self.raw.visible_bytes() + BYTES_PER_ROW
        end = min(self._offset + window, self._view_end())
        data = doc.data[self._offset : end]
        if self._reads_pointers():
            cells = self._pointer_cells(doc, self._offset, end)
            tokens, tips = hex_tokens(
                cells,
                self._offset,
                self._pointer_preview(doc, tables),
                self.resolve_pointers.isChecked(),
            )
            self.raw.set_model(
                RowModel(
                    self._offset,
                    data,
                    tokens,
                    set(),
                    doc.size,
                    bounds=self._bounds,
                    tips=tips,
                )
            )
            return
        run = self._decode_window(data, tables, self._offset)
        string_starts = {bit // 8 for bit in run.starts}
        pointer_bytes: set[int] = set()
        if block is not None and block.doc is not None:
            marked = [
                (p.address, p.size) for r in block.doc.strings for p in r.pointers
            ]
            if block.config is not None and isinstance(
                block.config.source, NestedPointerSource
            ):
                # The outer table's pointers reach no string of their own.
                marked += [
                    (c.address, c.size)
                    for c in self._pointer_cells(doc, self._offset, end)
                ]
            for address, size in marked:
                for b in range(address, address + size):
                    rel = b - self._offset
                    if 0 <= rel < len(data):
                        pointer_bytes.add(rel)
        self.raw.set_model(
            RowModel(
                self._offset,
                data,
                run.tokens,
                string_starts,
                doc.size,
                pointer_bytes,
                self._bounds,
            )
        )

    def _on_raw_rows_changed(self) -> None:
        """The raw view has room for a different number of rows: hand it that
        many, without the rest of a refresh."""
        if self._doc is not None:
            self._refresh_raw(self._doc, self._table_set())
            self._sync_steps()

    def _decode_window(
        self, data: bytes, tables: TableSet | None, offset: int = 0
    ) -> RunResult:
        """Decode one string after another over ``data`` until it runs out,
        each cut the way the reading cuts them, in step with the byte ``data``
        begins at."""
        if tables is None or not data:
            return RunResult([], 0)
        return decode_strings(data, self._reading(), tables, offset)

    def _pointer_cells(self, doc: Document, start: int, end: int) -> list[PointerCell]:
        """The reading's pointers that start in bytes ``start`` to ``end``."""
        source = view_source(
            self._reading(), self._current_block() is not None, start, self._view_end()
        )
        return pointer_cells(doc.data, source, start, end, self.registry)

    def _pointer_preview(self, doc: Document, tables: TableSet | None):
        """What a pointer's target reads as, through the strings' table.

        Each target is read once for as long as the data, the reading and the
        table stay what they are: a scroll shows the same pointers a row over,
        and the Hex and Text tabs the same ones.
        """
        cfg = self._reading()
        if tables is None or cfg is None:
            return preview_reader(None)
        key = (
            id(doc.data),
            cfg,
            tables.start.id,
            sum(len(t.entries) for t in tables.tables.values()),
        )
        if self._previews is None or self._previews[0] != key:
            self._previews = (key, Bits(doc.data), {})
        _, bits, seen = self._previews
        return preview_reader(lambda at: target_string(bits, cfg, tables, at), seen)

    def _pointer_stride(self) -> int:
        """Bytes from one pointer in view to the next."""
        source = self._reading().source
        return max(getattr(source, "stride", source.size), 1)

    def _current_view(self) -> str:
        """The open tab, as a session names it."""
        return VIEWS[max(self.tabs.currentIndex(), 0)]

    def _show_view(self, view: str) -> None:
        """Open the tab a session names; an unknown name opens Hex."""
        self.tabs.setCurrentIndex(VIEWS.index(view) if view in VIEWS else 0)

    def _on_tab_changed(self, _index: int) -> None:
        # Only the Text tab renders on arrival: it is skipped while hidden.
        if self._doc is not None and self.tabs.currentWidget() is self.text:
            self._refresh_text_mode(self._doc, self._table_set())
        if self._doc is not None:
            self._sync_steps()  # what fits is the open tab's to say

    def _on_text_selection(self, start: int, end: int) -> None:
        self.raw.set_selection(start, end)
        self._on_selection(start, end, from_text=True)

    def _update_nav_status(self) -> None:
        doc = self._doc
        if doc is None:
            return
        parts = [f"{doc.size:,} bytes"]
        if self._bounds is not None:
            start, end = self._bounds
            parts.append(
                f"viewing {self._format_address(start)}–"
                f"{self._format_address(end - 1)} ({end - start:,} bytes)"
            )
        if self._selection:
            s, e = self._selection
            parts.append(
                f"selected {self._format_address(s)}–"
                f"{self._format_address(e - 1)} ({e - s} bytes)"
            )
        if doc.missing_plugins:
            parts.append("view-only: missing " + ", ".join(doc.missing_plugins))
        self.nav_status.setText("  ·  ".join(parts))
