"""The Text tab's viewport: the window it fits, and moving it by lines."""

from __future__ import annotations

from bisect import bisect_right

from mapchar.core.document import Document
from mapchar.core.table import TableSet
from mapchar.core.tokens import Token
from mapchar.pipeline.pointers import pointer_window
from mapchar.pipeline.text_view import TextDecode, TextModel, text_model
from mapchar.pipeline.view_read import align_before, cuts_at_end_tokens
from mapchar.ui import BYTES_PER_ROW
from mapchar.ui.pointer_tokens import text_tokens, view_source

TEXT_WINDOW_LIMIT = 1 << 15
"""The most bytes the Text tab decodes to fill its box. Past this, a stretch of
the file that decodes to next to nothing is shown as far as it goes."""

_ALIGN_TRIES = 8
"""How many bytes back the Text tab tries decoding the text above its view
from, to find one in step."""
_ALIGN_LOOKAHEAD = 16
"""Bytes past the offset decoded to see whether a token starts there."""


class TextViewMixin:
    """The Text tab's viewport: the window it fits, and moving it by lines.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _refresh_text_mode(self, doc: Document, tables: TableSet | None) -> None:
        if self.tabs.currentWidget() is not self.text:
            return
        self.text.set_model(self._fit_text_window(doc, tables))
        self.text.set_position(self._offset, self._view_range())
        self.text.select_bytes(*(self._selection or (-1, -1)))

    def _fit_text_window(self, doc: Document, tables: TableSet | None) -> TextModel:
        """The Text tab's window: decoded from the offset until the text
        overflows the box, then cut back to the tokens in view.

        How many bytes fill a box of text cannot be known up front — a
        dictionary token is a word on one byte, a table switch is nothing on
        several — so the window starts at what the last one took, or, for the
        first, at the box's room in characters, and
        doubles until the text overflows, the file ends or
        :data:`TEXT_WINDOW_LIMIT` is reached, and is then cut to whole lines.
        Unwrapped, a last line cut at the box's edge with room under it has not
        overflowed: the text ran out inside it, and more may follow.
        The first token is always kept, so the window is never empty.
        """
        offset, end = self._offset, self._view_end()
        room = max(BYTES_PER_ROW, self.text.room())
        pointers = self._reads_pointers()
        if tables is None and not pointers:
            return text_model([], offset, min(offset + room, end) - offset)
        if pointers:
            # A line is a pointer, so the window is known: one more pointer
            # than the box has lines, which overflows it by a line.
            source = view_source(
                self._reading(), self._current_block() is not None, offset, end
            )
            until = pointer_window(
                source,
                offset,
                self.text.lines_in_view() + 1,
                doc.data,
                self.registry,
            )
            model = self._text_tokens(
                doc, tables, offset, end if until is None else until
            )
            self.text.set_model(model)
            return model.cut(bisect_right(model.columns()[1], self.text.fitted_chars()))
        # The last window is the best guess at this one: as many bytes as
        # overflowed then, or, where the first try overflowed, twice what was
        # kept, so a guess grown over a stretch that decodes to little shrinks
        # back once the text is dense again. The box's room is only the first
        # guess, before there is a window to go on — it counts characters, and
        # text that spells a byte [$XX] fills the box on a fraction of them.
        length = self._text_guess or room
        tries = 0
        while True:
            tries += 1
            stop = min(offset + length, end)
            model = self._text_tokens(doc, tables, offset, stop)
            self.text.set_model(model)
            fitted = self.text.fitted_chars()
            overflows = fitted < len(model.body) and not self.text.room_below()
            if overflows or stop >= end or length >= TEXT_WINDOW_LIMIT:
                break
            length *= 2
        model = model.cut(bisect_right(model.columns()[1], fitted))
        self._text_guess = 2 * model.length if overflows and tries == 1 else length
        return model

    def _text_tokens(
        self, doc: Document, tables: TableSet | None, offset: int, stop: int
    ) -> TextModel:
        """The text from ``offset`` to ``stop``, from the tokens kept since the
        last window where they serve, decoding only what they do not reach
        (:class:`~mapchar.pipeline.text_view.TextDecode`). Read as pointers, a line
        per pointer."""
        if self._reads_pointers():
            cells = self._pointer_cells(doc, offset, stop)
            tokens = text_tokens(
                cells,
                offset,
                self._pointer_preview(doc, tables),
                self.resolve_pointers.isChecked(),
            )
            return text_model(tokens, offset, stop - offset)
        shown = self.text.shown()
        cache = self._text_decode
        if (
            cache is not None
            and cache.serves(doc.data, tables, shown)
            and cache.can_serve(offset)
        ):
            cache.extend(stop, self._decode_window)
            model = cache.model(offset, stop)
            if model is not None:
                return model
        cache = self._text_decode = TextDecode(
            doc.data,
            tables,
            offset,
            shown=shown,
            resumable=cuts_at_end_tokens(self._reading()),
        )
        cache.extend(stop, self._decode_window)
        model = cache.model(offset, stop)
        assert model is not None
        return model

    def _on_text_fit_changed(self) -> None:
        """The Text tab's box has room for a different window: fit one to it."""
        self._text_guess = 0
        if self._doc is not None:
            self._refresh_text_mode(self._doc, self._table_set())
            self._sync_steps()

    def _on_text_shown_changed(self) -> None:
        """Show codes or Show unknown was switched: the kept tokens read to
        another text now, so they are dropped and the window fitted afresh."""
        self._text_decode = None
        self._on_text_fit_changed()

    def _on_text_scroll(self, lines: int) -> None:
        """Move the Text tab's view by that many lines: the wheel, the keys,
        the row and page steps and the scrollbar's arrows and trough all do.

        Always to where a line starts, on a token the text in view already
        decodes: a byte count would start the view inside a line, re-wrapping
        everything, or inside a token, decoding the rest out of step — and
        either way the same bytes read and highlight differently.

        Down is the start of a line in view. Up is a line of the text decoded
        before the offset, laid out in the box; that text has no known line
        start of its own, so up retraces the steps down that led here when
        they did, and only measures when they did not.
        """
        doc = self._doc
        if doc is None or not lines:
            return
        trail = self._text_trail
        if trail and trail[-1][1] != self._offset:
            trail.clear()
        before = self._offset
        if lines < 0 and self._reads_pointers():
            # A line is a pointer, so a line up is a stride back.
            first = self._view_range()[0]
            target = max(first, self._offset + lines * self._pointer_stride())
        elif lines > 0:
            target = self._text_lines_down(lines, doc)
        elif trail and trail[-1][2] == -lines:
            target = trail.pop()[0]
        else:
            target = self._text_lines_up(-lines, doc, self._table_set())
        self._go_to(target)
        if self._offset == before:
            # Measuring up leaves its text in the box; put the window back.
            self._refresh_text_mode(doc, self._table_set())
        elif lines > 0:
            trail.append((before, self._offset, lines))

    def _text_lines_down(self, lines: int, doc: Document) -> int:
        """The byte the view starts at ``lines`` lines down: the start of that
        line in view, or the end of the window. No further once the window
        reaches the end of the file — or of the view's bounds — as the Hex tab
        stops at its last page."""
        text, offset = self.text, self._offset
        end = offset + text.shown_bytes()
        if end >= self._view_end():
            return offset
        for start in text.line_starts()[lines:]:
            byte = text.byte_at_char(start)
            # A token as long as the lines above it starts at the offset itself.
            if byte is not None and offset < byte < end:
                return byte
        return end

    def _text_lines_up(self, lines: int, doc: Document, tables: TableSet | None) -> int:
        """The byte the view starts at ``lines`` lines up.

        The text before the offset is laid out in the box followed by the
        view's first line, so a line the offset falls inside counts as the
        view's own; it is decoded from further back each time until there are
        that many lines above, so the first — which starts wherever the decode
        did — is never the one landed on.
        """
        offset, text = self._offset, self.text
        first = self._view_range()[0]
        if offset <= first or tables is None:
            return max(first, offset - lines * BYTES_PER_ROW)
        shown = text.shown_bytes()
        per_line = shown / text.lines_in_view() if shown else BYTES_PER_ROW
        body, starts = text.edit.toPlainText(), text.line_starts()
        first_line = body[: starts[1]] if len(starts) > 1 else body
        # As far back as the last step up had to go for a line, when that is
        # further than twice the text in view suggests: over a stretch that
        # decodes to few lines, or to one, each step would otherwise decode
        # from a little further back over and over to find the same thing.
        per_line = max(per_line * 2, self._text_up_guess)
        budget = min(
            max(BYTES_PER_ROW, round((lines + 1) * per_line)), TEXT_WINDOW_LIMIT
        )
        shown = self.text.shown()
        cache = self._text_decode
        if cache is not None and not cache.serves(doc.data, tables, shown):
            cache = None
        while True:
            # The kept tokens serve the text above where they reach back to;
            # what they do not is decoded, and joins them for the next step.
            above = cache.above(max(first, offset - budget), offset) if cache else None
            if above is None:
                start, tokens, starts = self._decode_up_to(
                    doc, tables, offset - budget, offset
                )
                above = text_model(tokens, start, offset - start, shown, starts)
                if cache is not None:
                    cache.prepend(start, tokens, starts)
            start = above.offset
            text.set_model(
                TextModel(above.body + first_line, above.spans, start, offset - start)
            )
            starts = text.line_starts()
            here = max(i for i, s in enumerate(starts) if s <= len(above.body))
            if here > lines or start <= first or budget >= TEXT_WINDOW_LIMIT:
                break
            budget *= 2
        self._text_up_guess = budget / (lines + 1) if here > lines else budget
        byte = text.byte_at_char(starts[max(0, here - lines)])
        return start if byte is None else byte

    def _decode_up_to(
        self, doc: Document, tables: TableSet, start: int, offset: int
    ) -> tuple[int, list[Token], list[int]]:
        """The tokens from about ``start`` up to ``offset``, and the bit each of
        their strings begins at, decoded from whichever of the few bytes back
        from ``start`` reads best
        (:func:`~mapchar.pipeline.view_read.align_before`)."""
        return align_before(
            doc.data,
            self._view_range()[0],
            start,
            offset,
            lambda data, at: self._decode_window(data, tables, at),
            tries=_ALIGN_TRIES,
            lookahead=_ALIGN_LOOKAHEAD,
        )
