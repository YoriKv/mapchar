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
        if entry is None or entry.box is None:
            self._error("Give the block a text box first, in the Preview window.")
            return
        box = self._layout_box(entry)
        newline = next(
            (lb for lb, e in box.effects.items() if e.effect.value == "newline"),
            None,
        )
        page = next(
            (lb for lb, e in box.effects.items() if e.effect.value == "page"),
            None,
        )
        if newline is None:
            self._error(
                "Give one code the 'newline' effect, in its table entry or the "
                "Codes tab, first."
            )
            return
        indices = self.strings.selected_indices() or [
            r.index for r in entry.doc.strings[:1]
        ]
        texts = {}
        for index in indices:
            rec = self._string(entry, index)
            if rec is not None:
                texts[index] = rec.current_text()
        # Measured once for every string about to be wrapped, so the font
        # knows each character before the first word is placed.
        font = self._layout_font(box, *texts.values())
        edits = {}
        for index, text in texts.items():
            wrapped, _ = wrap_text(text, font, box, newline, page)
            if wrapped != text:
                edits[index] = wrapped
        if not edits:
            return
        problems = self._edit_strings(entry, edits, "Wrap")
        if problems:
            self._refuse_edit(problems)
