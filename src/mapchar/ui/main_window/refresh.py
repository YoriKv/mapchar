"""The refresh cycle: the single choke point after any change."""

from __future__ import annotations

from mapchar.core.bits import Bits
from mapchar.core.block import (
    EndToken,
    FixedLength,
    FixedSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
)
from mapchar.core.document import Document
from mapchar.core.table import TableSet
from mapchar.engines.decode import DecodeRules, RunResult, decode_run
from mapchar.project.workspace import EntryKind
from mapchar.ui import BYTES_PER_ROW
from mapchar.ui.raw_widget import RowModel
from mapchar.ui.text_widget import TextModel, text_model

VIEWS = ("raw", "text", "strings")
"""A session's name for each central tab, in tab order: Hex, Text, Strings."""

TEXT_WINDOW_LIMIT = 1 << 15
"""The most bytes the Text tab decodes to fill its box. Past this, a stretch of
the file that decodes to next to nothing is shown as far as it goes."""

_KIND_NAMES = {
    RangeSource: "Range",
    FixedSource: "Fixed strings",
    PointerTableSource: "Pointer table",
    PointerListSource: "Pointer list",
    EndToken: "End token",
    FixedLength: "Fixed length",
    Pascal: "Pascal",
    NextPointer: "Next pointer",
}
"""What the block bar calls a source or string type: the Block dialog's words
for it, never the class name."""


class RefreshMixin:
    """The refresh cycle: the single choke point after any change.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _refresh_view(self) -> None:
        doc, entry = self._doc, self._entry
        is_block = entry is not None and entry.kind is EntryKind.BLOCK
        if doc is None:
            self.raw.set_model(None)
            self.strings.set_rows([])
            self.nav_status.setText("")
            self.offset_box.setText("")
            self._update_title()
            self._sync_capabilities()
            return
        total = doc.size
        self._offset = max(0, min(self._offset, max(total - 1, 0)))
        self.offset_box.setText(self._format_address(self._offset))
        tables = self._table_set()
        if is_block:
            self._extract_current(entry, doc, tables)
        self._refresh_raw(doc, tables)
        self._refresh_text_mode(doc, tables)
        self._refresh_decompress_preview(doc, tables)
        if is_block:
            self._fill_strings(doc)
            cfg = entry.config
            count = len(doc.strings)
            self.block_label.setText(
                f"{entry.name}: "
                f"{_KIND_NAMES.get(type(cfg.source), 'Source')} · "
                f"{_KIND_NAMES.get(type(cfg.string_type), 'Strings')} · "
                f"@{cfg.table_id or '-'} · "
                f"{count} {'string' if count == 1 else 'strings'}"
            )
        self._update_nav_status()
        self.search_window.set_data(doc.data)
        self.scan_window.set_source(doc.data, tables)
        self._sync_hex_panel()
        self._update_title()
        # Last, so the capability table has the final word: every pass above
        # enables controls on grounds that are true in general and beside the
        # point for an entry of the wrong kind.
        self._sync_capabilities()

    def _refresh_raw(self, doc: Document, tables: TableSet | None) -> None:
        """The raw view's window: as many rows as it shows, and one more for
        the row cut off at its bottom edge."""
        entry = self._entry
        window = self.raw.visible_bytes() + BYTES_PER_ROW
        data = doc.data[self._offset : self._offset + window]
        run = self._decode_window(data, tables)
        string_starts = {bit // 8 for bit in run.starts}
        pointer_bytes: set[int] = set()
        is_block = entry is not None and entry.kind is EntryKind.BLOCK
        if is_block and entry.doc is not None:
            for rec in entry.doc.strings:
                for p in rec.pointers:
                    for b in range(p.address, p.address + p.size):
                        rel = b - self._offset
                        if 0 <= rel < len(data):
                            pointer_bytes.add(rel)
        self.raw.set_model(
            RowModel(
                self._offset, data, run.tokens, string_starts, doc.size, pointer_bytes
            )
        )

    def _on_raw_rows_changed(self) -> None:
        """The raw view has room for a different number of rows: hand it that
        many, without the rest of a refresh."""
        if self._doc is not None:
            self._refresh_raw(self._doc, self._table_set())

    @staticmethod
    def _decode_window(data: bytes, tables: TableSet | None) -> RunResult:
        """Decode one string after another over ``data`` until it runs out."""
        if tables is None or not data:
            return RunResult([], 0)
        return decode_run(
            Bits(data),
            tables,
            rules=DecodeRules(end_terminated=True),
            runs=None,
            ends_only=False,
        )

    def _refresh_text_mode(self, doc: Document, tables: TableSet | None) -> None:
        if self.tabs.currentWidget() is not self.text:
            return
        self.text.set_model(self._fit_text_window(doc, tables))
        if self._selection:
            self.text.select_bytes(*self._selection)

    def _fit_text_window(self, doc: Document, tables: TableSet | None) -> TextModel:
        """The Text tab's window: decoded from the offset until the text
        overflows the box, then cut back to the tokens in view.

        How many bytes fill a box of text cannot be known up front — a
        dictionary token is a word on one byte, a table switch is nothing on
        several — so the window starts at the box's room in characters and
        doubles until the text overflows, the file ends or
        :data:`TEXT_WINDOW_LIMIT` is reached, and is then cut to whole lines.
        The first token is always kept, so the window is never empty.
        """
        offset = self._offset
        length = max(BYTES_PER_ROW, self.text.room())
        if tables is None:
            data = doc.data[offset : offset + length]
            return text_model([], offset, len(data))
        while True:
            data = doc.data[offset : offset + length]
            tokens = self._decode_window(data, tables).tokens
            model = text_model(tokens, offset, len(data))
            self.text.set_model(model)
            fitted = self.text.fitted_chars()
            if (
                fitted < len(model.body)
                or len(data) < length
                or length >= TEXT_WINDOW_LIMIT
            ):
                break
            length *= 2
        kept = 0
        for _start, end, _byte_start, _byte_end in model.spans:
            if end > fitted:
                break
            kept += 1
        kept = max(kept, 1) if tokens else 0
        if kept == len(tokens):
            return model
        return text_model(tokens[:kept], offset, model.spans[kept - 1][3] - offset)

    def _on_text_fit_changed(self) -> None:
        """The Text tab's box has room for a different window: fit one to it."""
        if self._doc is not None:
            self._refresh_text_mode(self._doc, self._table_set())

    def _on_text_scroll(self, lines: int) -> None:
        """The wheel turned over the Text tab: move by that many lines' worth
        of bytes, taking the window's own bytes per line as the measure."""
        shown = self.text.shown_bytes()
        per_line = shown / self.text.lines_in_view() if shown else BYTES_PER_ROW
        step = max(1, round(abs(lines) * per_line))
        self._move(step if lines > 0 else -step)

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

    def _on_text_selection(self, start: int, end: int) -> None:
        self.raw.set_selection(start, end)
        self._on_selection(start, end, from_text=True)

    def _update_nav_status(self) -> None:
        doc = self._doc
        if doc is None:
            return
        parts = [f"{doc.size:,} bytes"]
        if self._selection:
            s, e = self._selection
            parts.append(f"selected {s:X}–{e - 1:X} ({e - s} bytes)")
        if doc.missing_plugins:
            parts.append("view-only: missing " + ", ".join(doc.missing_plugins))
        self.nav_status.setText("  ·  ".join(parts))
