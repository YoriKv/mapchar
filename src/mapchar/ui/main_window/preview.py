"""The Preview window and the text box it draws."""

from __future__ import annotations

from mapchar.core.font import CodeEffect, Font, TextBox
from mapchar.core.tokens import Token
from mapchar.engines.layout import code_effects, with_code_effects
from mapchar.project.workspace import Entry
from mapchar.ui.preview_font import preview_font
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
        if entry.box is None:
            # The first Preview of a block gives it a box; that is an edit the
            # project saves, so it is an undo step too.
            self._push_command(BoxCommand(self, entry, None, TextBox()))
        self._sync_preview(force=True)
        self.preview_window.show()
        self.preview_window.raise_()

    def _sync_preview(self, force: bool = False) -> None:
        if not (force or self.preview_window.isVisible()):
            return
        entry = self._current_block(need_doc=True)
        if entry is None:
            return
        self.preview_window.set_box(
            entry.box or TextBox(), self._code_labels(), self._code_effects_of(entry)
        )
        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        if rec is None and entry.doc.strings:
            rec = entry.doc.strings[0]
        if rec is not None:
            self.preview_window.show_string(
                rec.current_text(), f"{entry.name} #{rec.index}"
            )

    def _on_box_changed(self, box: TextBox) -> None:
        entry = self._current_block()
        if entry is None or entry.box == box:
            return
        self._push_command(BoxCommand(self, entry, entry.box, box))

    def _on_preview_font_changed(self) -> None:
        """The app's preview font changed: every surface measured through it —
        the *overflows box* status above all — is drawn again."""
        self._refresh_view()

    def apply_box(self, entry: Entry, box: TextBox | None, revision: int) -> None:
        entry.box = box
        self.workspace.stamp(entry, revision)
        if self._applying_undo:
            self.preview_window.set_box(
                box or TextBox(), self._code_labels(), self._code_effects_of(entry)
            )
        self.preview_window.update_box(box or TextBox())
        self._refresh_view()

    def _code_effects_of(self, entry: Entry | None) -> dict[str, CodeEffect]:
        """What the codes ``entry`` reads through do to layout before its box
        says otherwise: the effects their entries declare, and its line code."""
        if entry is None:
            return {}
        cfg = entry.config
        tables = self._table_set_of(entry)
        return code_effects(tables, cfg.line_label if cfg is not None else "line")

    def _layout_font(
        self, box: TextBox | None, *sources: list[Token] | str
    ) -> Font | None:
        """The font ``box`` lays out through: the app's preview font, measured
        for everything ``sources`` draws.

        ``None`` where the box counts characters instead — *chars per line* is
        what a block whose own font is on a grid says so with, and counting is
        then truer to it than measuring a stand-in family.
        """
        if box is None or box.chars_per_line > 0:
            return None
        return preview_font().measured(*sources)

    def _layout_box(self, entry: Entry | None) -> TextBox | None:
        """``entry``'s box as layout sees it: the codes' effects from its
        tables under what the box itself sets."""
        if entry is None or entry.box is None:
            return None
        return with_code_effects(entry.box, self._code_effects_of(entry))

    def _code_labels(self) -> list[str]:
        """Every code label of the current table set, for the Codes tab."""
        tables = self._table_set()
        return sorted(
            {lb for t in (tables.tables.values() if tables else ()) for lb in t.labels}
        )
