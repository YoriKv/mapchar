"""Editing one string: its translation, its notes and its status."""

from __future__ import annotations

from mapchar.core.block import EndToken, Status, block_bound
from mapchar.core.errors import MapcharError
from mapchar.ui.undo_commands import StringFieldCommand


class StringEditMixin:
    """Editing one string: its translation, its notes and its status.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _on_translation_edited(self, index: int, text: str) -> None:
        self._set_translation(self._entry, index, text, refresh=True)

    def _set_translation(
        self, entry, index: int, text: str, *, refresh: bool = False
    ) -> None:
        """Record a translation edit undoably; blank text, or the original
        text again, clears the translation."""
        rec = self._string(entry, index)
        if rec is None:
            return
        after = text if text.strip() else None
        if after is not None and rec.matches_original(after):
            after = None
        if after == rec.translation:
            if refresh:
                self._refresh_string_row(entry, index)
            return
        self._push_command(
            StringFieldCommand(
                self,
                entry,
                index,
                "translation",
                rec.translation,
                after,
                run=self._edit_run,
            )
        )

    def _on_notes_edited(self, index: int, text: str) -> None:
        entry = self._entry
        rec = self._string(entry, index)
        if rec is None or text == rec.notes:
            return
        self._push_command(
            StringFieldCommand(
                self, entry, index, "notes", rec.notes, text, run=self._edit_run
            )
        )

    def apply_string_field(
        self, entry, index: int, field: str, value, revision: int
    ) -> None:
        """Land one field of one string, at the revision that half of the step
        leaves the entry at.

        ``revision`` is the token the command captured rather than a fresh one:
        an undo hands back exactly the unsaved-state the entry had before the
        edit, so undoing back to what was written reads clean again.
        """
        rec = self._string(entry, index)
        if rec is None:
            return
        if field == "translation":
            rec.translation = value
            if value is None:
                rec.status = Status.UNTOUCHED
            elif rec.status is Status.UNTOUCHED:
                rec.status = Status.EDITED
        elif field == "notes":
            rec.notes = value
        elif field == "status":
            rec.status = Status(value)
        self.workspace.stamp(entry, revision)
        self._refresh_string_row(entry, index)
        self.files_panel.refresh_labels()
        self._update_title()

    def _on_draft(self, text: str) -> None:
        """The Translation cell as it is typed: a live encode and a live preview.

        The draft never touches the document — it lands on the Preview, on the
        byte readout, and on the Preview's list of what the font cannot spell.
        """
        entry = self._entry
        tables = self._table_set()
        if entry is None or entry.config is None or tables is None:
            return
        from mapchar.engines.encode import encode

        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        if self.preview_window.isVisible() and rec is not None:
            self.preview_window.show_string(text, f"{entry.name} #{rec.index}")
        try:
            result = encode(
                text,
                tables,
                end_terminated=isinstance(entry.config.string_type, EndToken),
                ends=entry.config.strings_per_pointer,
            )
        except MapcharError as exc:
            self.statusBar().showMessage(str(exc))
            self.preview_window.set_readout(str(exc))
            return
        used = -(-len(result.bits) // 8)
        room = (
            self._room(rec, entry.config, block_bound(entry.config, entry.doc.strings))
            if rec
            else 0
        )
        readout = f"{used} / {room} byte(s)" if room else f"{used} byte(s)"
        if room and used > room:
            readout += f" — {used - room} over"
        self.statusBar().showMessage(readout)
        self.preview_window.set_readout(readout)
