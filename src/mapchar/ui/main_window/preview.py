"""The Preview window and the text box it draws."""

from __future__ import annotations

from mapchar.core.font import TextBox
from mapchar.core.table import TokenKind
from mapchar.core.tokens import plain_text
from mapchar.project.workspace import Entry
from mapchar.ui.undo_commands import BoxCommand


class PreviewMixin:
    """The Preview window and the text box it draws.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_preview(self) -> None:
        entry = self._current_block(complain="Select a block to preview.")
        if entry is None:
            return
        fonts = self.workspace.fonts()
        if entry.box is None:
            entry.box = TextBox(font_index=0 if fonts else None)
        elif entry.box.font_index is None and fonts:
            from dataclasses import replace

            entry.box = replace(entry.box, font_index=0)
        if not fonts:
            self._error("Open a font (File ▸ Open Font…) first.")
        self._sync_preview(force=True)
        self.preview_window.show()
        self.preview_window.raise_()

    def _sync_preview(self, force: bool = False) -> None:
        if not (force or self.preview_window.isVisible()):
            return
        entry = self._current_block(need_doc=True)
        if entry is None:
            return
        font_entry = self._bound_font(entry)
        self.preview_window.set_font(font_entry.font if font_entry else None)
        self.preview_window.set_box(entry.box or TextBox(), self._code_labels())
        self.preview_window.set_table_chars(self._table_chars())
        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        if rec is None and entry.doc.strings:
            rec = entry.doc.strings[0]
        if rec is not None:
            source = rec.translation if rec.translation is not None else rec.original
            self.preview_window.show_string(source, f"{entry.name} #{rec.index}")

    def _on_box_changed(self, box: TextBox) -> None:
        entry = self._current_block()
        if entry is None:
            return
        from dataclasses import replace

        after = replace(
            box, font_index=entry.box.font_index if entry.box else box.font_index
        )
        if entry.box == after:
            return
        self._push_command(BoxCommand(self, entry, entry.box, after))

    def apply_box(self, entry: Entry, box: TextBox | None, revision: int) -> None:
        entry.box = box
        self.workspace.stamp(entry, revision)
        if self._applying_undo:
            self.preview_window.set_box(box or TextBox(), self._code_labels())
        self.preview_window.update_box(box or TextBox())
        self._refresh_view()

    def _table_chars(self) -> str:
        """The start table's one-character text entries in key order.

        What the Font tab's *Fill from table* lays over the sheet: a table
        whose codes run in the sheet's order spells the font in one gesture.
        """
        tables = self._table_set()
        if tables is None:
            return ""
        out = []
        for e in tables.start.sorted_entries():
            if e.kind is TokenKind.TEXT and e.label is None:
                text = plain_text(e.text)
                if len(text) == 1:
                    out.append(text)
        return "".join(out)

    def _code_labels(self) -> list[str]:
        """Every code label of the current table set, for the Codes tab."""
        tables = self._table_set()
        return sorted(
            {lb for t in (tables.tables.values() if tables else ()) for lb in t.labels}
        )
