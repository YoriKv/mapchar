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
from mapchar.ui import BYTES_PER_ROW, TEXT_WINDOW_BYTES
from mapchar.ui.raw_widget import RowModel
from mapchar.ui.text_widget import text_model

DISPLAY_MODE_KEY = "view/display_mode"

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
        window = self.raw.visible_bytes() + BYTES_PER_ROW
        data = doc.data[self._offset : self._offset + window]
        run = self._decode_window(data, tables)
        tokens = run.tokens
        string_starts = {bit // 8 for bit in run.starts}
        pointer_bytes: set[int] = set()
        if is_block and entry.doc is not None:
            for rec in entry.doc.strings:
                for p in rec.pointers:
                    for b in range(p.address, p.address + p.size):
                        rel = b - self._offset
                        if 0 <= rel < len(data):
                            pointer_bytes.add(rel)
        self.raw.set_model(
            RowModel(self._offset, data, tokens, string_starts, total, pointer_bytes)
        )
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
        if self.display.currentWidget() is not self.text:
            return
        data = doc.data[self._offset : self._offset + TEXT_WINDOW_BYTES]
        tokens = self._decode_window(data, tables).tokens
        self.text.set_model(text_model(tokens, self._offset, len(data)))
        if self._selection:
            self.text.select_bytes(*self._selection)

    def _show_display_mode(self, text_mode: bool) -> None:
        self.display.setCurrentWidget(self.text if text_mode else self.raw)
        self.mode_button.setText("Text" if text_mode else "Aligned")

    def _on_mode_toggled(self, text_mode: bool) -> None:
        self._show_display_mode(text_mode)
        self.settings.setValue(DISPLAY_MODE_KEY, "text" if text_mode else "aligned")
        self._refresh_view()

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
