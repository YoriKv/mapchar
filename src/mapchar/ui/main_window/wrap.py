"""Wrapping a translation to the block's text box."""

from __future__ import annotations

from mapchar.core.capabilities import Capability
from mapchar.engines.layout import wrap as wrap_text


class WrapMixin:
    """Wrapping a translation to the block's text box.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _wrap_selected(self) -> None:
        # Wrapping is a string surface, so it is the block's capability that says
        # whether it applies at all — the Preview window stays open across an
        # entry switch and its button would otherwise still act on the last one.
        if not self._can(Capability.WRAP):
            return
        entry = self._entry
        font_entry = self._bound_font(entry)
        font = font_entry.font if font_entry is not None else None
        # A block with no font wraps by characters, when its box says how
        # many a line holds.
        if entry is None or entry.box is None:
            self._error("Bind a font and a text box first.")
            return
        if font is None and entry.box.chars_per_line <= 0:
            self._error("Bind a font, or set the box's chars per line, first.")
            return
        newline = next(
            (lb for lb, e in entry.box.effects.items() if e.effect.value == "newline"),
            None,
        )
        page = next(
            (lb for lb, e in entry.box.effects.items() if e.effect.value == "page"),
            None,
        )
        if newline is None:
            self._error("Give one code the 'newline' effect in the Codes tab first.")
            return
        indices = self.strings.selected_indices() or [
            r.index for r in entry.doc.strings[:1]
        ]
        edits = {}
        for index in indices:
            rec = self._string(entry, index)
            if rec is None:
                continue
            text = rec.current_text()
            wrapped, _ = wrap_text(text, font, entry.box, newline, page)
            if wrapped != text:
                edits[index] = wrapped
        if not edits:
            return
        problems = self._edit_strings(entry, edits, "Wrap")
        if problems:
            self._refuse_edit(problems)
