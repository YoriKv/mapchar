"""What the Text tab shows of a decode, and the tokens it is served from.

Beside :mod:`mapchar.pipeline.view_read`, which reads a view's bytes: this
turns the tokens that come back into a body and a map from characters to
bytes, and keeps the tokens from one window to the next so a step down decodes
only what lies past them. No Qt: the Text tab (:mod:`mapchar.ui.text_widget`)
only draws what is built here.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from mapchar.core.table import TableSet, TokenKind
from mapchar.core.tokens import Token
from mapchar.engines.decode import RunResult


@dataclass(frozen=True)
class Shown:
    """What of a decode the Text tab shows: all of it, or with the codes or the
    unmatched bytes left out.

    A code is a bracketed token: a CODE entry, or an END or SWITCH entry whose
    text is exactly ``[label]``, which keeps the line breaks after it so the
    lines stay where they were. Unknown is what matched no entry, shown
    ``[$XX]`` — or ``[%bits]`` for a tail shorter than a byte.

    An end token ends its line, whether or not its table text breaks it; so
    does the block's line code, which renders with its break. A string cut by
    its length ends in no token of its own, so the break there is put in by
    :func:`text_model` from where the strings start.
    """

    codes: bool = True
    unknown: bool = True

    def text(self, token: Token) -> str:
        entry = token.entry
        if entry is None:
            return token.text() if self.unknown else ""
        text = self._visible(token)
        if token.is_end and not token.fallback:
            return text if text.endswith("\n") else text + "\n"
        return text

    def _visible(self, token: Token) -> str:
        text = token.text()
        entry = token.entry
        if self.codes or token.fallback:
            return text
        if entry.kind is TokenKind.CODE:
            return ""
        label = entry.label
        if label is not None and text.startswith(f"[{label}]"):
            return text[len(label) + 2 :]
        return text


ALL = Shown()
"""Everything shown: the default."""


@dataclass
class TextModel:
    body: str
    spans: list[tuple[int, int, int, int]]
    """``(char_start, char_end, byte_start, byte_end)`` per token, absolute bytes."""
    offset: int
    """The byte the text starts at."""
    length: int
    """How many bytes the text covers."""
    _columns: tuple[list[int], ...] | None = field(default=None, repr=False)

    def columns(self) -> tuple[list[int], list[int], list[int], list[int]]:
        """The spans by column — char starts, char ends, byte starts, byte
        ends — each in order, so a position is found by bisection rather than
        by walking every token of a window."""
        if self._columns is None:
            self._columns = tuple(
                list(column) for column in zip(*self.spans, strict=True)
            ) or ([], [], [], [])
        return self._columns

    def cut(self, kept: int) -> TextModel:
        """The first ``kept`` tokens, at least one, as a model of their own."""
        if kept >= len(self.spans):
            return self
        kept = max(kept, 1)
        last = self.spans[kept - 1]
        return TextModel(
            self.body[: last[1]], self.spans[:kept], self.offset, last[3] - self.offset
        )


def _byte_span(bit_start: int, bit_end: int) -> tuple[int, int]:
    """The absolute bytes a token over absolute bits touches, at least the one
    it starts in."""
    byte_start = bit_start // 8
    return byte_start, max(byte_start + 1, -(-bit_end // 8))


def _break_before(texts: list[str]) -> int:
    """End the text so far with a line break: a string ended where the next one
    starts, and without the break the two would read as one string. The token
    it went on, or ``-1`` for a text that ends in one already or is empty.

    It goes on the last token that renders to anything, which is the one the
    character belongs to; the tokens after it render to nothing at all.
    """
    i = len(texts) - 1
    while i >= 0 and not texts[i]:
        i -= 1
    if i < 0 or texts[i].endswith("\n"):
        return -1
    texts[i] += "\n"
    return i


def text_model(
    tokens: list[Token],
    offset: int,
    length: int,
    shown: Shown = ALL,
    starts: list[int] | tuple[int, ...] = (),
) -> TextModel:
    """Render tokens (bit positions relative to ``offset``) to a body and map.

    ``starts`` is the bit each string begins at, in the same frame: the line
    breaks between strings the tokens do not carry themselves. The first of
    them starts the tokens, not a string, so nothing breaks there.
    """
    texts: list[str] = []
    breaks = set(starts[1:])
    for token in tokens:
        if token.bit_start in breaks:
            _break_before(texts)
        texts.append(shown.text(token))
    spans: list[tuple[int, int, int, int]] = []
    at = 0
    base = offset * 8
    for token, text in zip(tokens, texts, strict=True):
        byte_start, byte_end = _byte_span(base + token.bit_start, base + token.bit_end)
        spans.append((at, at + len(text), byte_start, byte_end))
        at += len(text)
    return TextModel("".join(texts), spans, offset, length)


class TextDecode:
    """The Text tab's decode, kept from one window to the next.

    A window that has moved a line down is nearly the window before it: the
    same tokens from a little further on, and a line's worth more at the end.
    So the tokens are kept, by their absolute bits, and a window is served from
    them wherever they reach; only what lies past them is decoded, from the
    last token boundary they can be trusted to. The text above a window, which
    a step up lays out to find the line to land on, is kept the same way: once
    decoded it joins the tokens in front, and a later step up over it decodes
    nothing.

    Decoding from a byte in the middle of the kept tokens gives the same tokens
    only where the decoder carries no state across a token: a set with no
    table switch, where every token boundary on a byte is a fresh start. With
    switches, the kept tokens serve a window from the byte they were decoded
    from and no other, as a fresh decode would.

    Tokens near a cut end are not to be trusted: a key cut short by the end of
    the data reads as unmatched bytes, and a code's operand as nothing. So a
    decode that goes further starts from the last token boundary at least
    ``margin`` bits — the longest key and operands of the set — before the cut.
    """

    def __init__(
        self,
        data: bytes,
        tables: TableSet,
        origin: int,
        *,
        shown: Shown = ALL,
        resumable: bool = True,
    ) -> None:
        self.data = data
        self.tables = tables
        self.shown = shown
        self.origin = origin
        """The byte the first token starts at."""
        self.end = origin
        """The byte the decoded data ended at, exclusive."""
        self.starts: list[int] = []
        self.ends: list[int] = []
        """Each token's absolute bits."""
        self.byte_starts: list[int] = []
        self.byte_ends: list[int] = []
        """The absolute bytes each token touches, at least the one it starts in."""
        self.texts: list[str] = []
        self.chars: list[int] = [0]
        """The character each token's text starts at, and after the last the
        length of them all."""
        # A string cut by length rather than at an end token starts where the
        # one before it ended, not at any token boundary. Each table answers
        # for itself and remembers it: a view that moves builds one of these
        # per window, and a charset has thousands of entries.
        self.resumable = resumable and not any(
            t.has_switch() for t in tables.tables.values()
        )
        self.margin = max((t.key_span() for t in tables.tables.values()), default=8)

    def serves(self, data: bytes, tables: TableSet, shown: Shown) -> bool:
        """Whether these tokens are of ``data`` read through ``tables``, shown
        as ``shown`` says."""
        return (
            shown == self.shown
            and data is self.data
            and tables.start is self.tables.start
            and tables.tables == self.tables.tables
        )

    def can_serve(self, offset: int) -> bool:
        """Whether a window from ``offset`` could be served from these tokens.

        Asked before growing them, since growing them cannot make an offset
        they do not reach a token boundary: a view that jumped elsewhere —
        the scrollbar dragged, an address typed — decodes its window once
        rather than first decoding on from where the last one was.
        """
        return self._first(offset) is not None

    def _first(self, offset: int) -> int | None:
        """The index of the token a window from ``offset`` starts with, when
        one can: the token starting there, if decoding afresh from there would
        read the same."""
        if offset == self.origin:
            return 0
        if not self.resumable:
            return None
        i = bisect_left(self.starts, offset * 8)
        if i < len(self.starts) and self.starts[i] == offset * 8:
            return i
        return None

    def _trusted(self) -> int:
        """How many tokens from the start are read whole: every one that ends
        on a byte at least ``margin`` bits before the cut, and the tokens
        before it."""
        cut = self.end * 8 - self.margin
        i = bisect_right(self.ends, cut)
        while i > 0 and self.ends[i - 1] % 8:
            i -= 1
        return i

    def extend(self, stop: int, decode) -> None:
        """Have the tokens reach ``stop``: decode the rest, from the last
        trusted boundary — or, where no boundary is a fresh start, from the
        origin."""
        if stop <= self.end:
            return
        kept = self._trusted() if self.resumable else 0
        resume = self.ends[kept - 1] // 8 if kept else self.origin
        del self.starts[kept:], self.ends[kept:], self.texts[kept:]
        del self.byte_starts[kept:], self.byte_ends[kept:], self.chars[kept + 1 :]
        run: RunResult = decode(self.data[resume:stop], self.tables, resume)
        self._append(resume, run.tokens, run.starts)
        self.end = stop

    def _append(
        self, base: int, tokens: list[Token], starts: list[int] | tuple[int, ...] = ()
    ) -> None:
        """Tokens relative to byte ``base``, after those kept; ``starts`` is the
        bit each string begins at, in the same frame.

        The first of them starts the decode, not a string: it resumes whatever
        string the tokens before it were part of, so nothing breaks there.
        """
        base *= 8
        breaks = {base + start for start in starts[1:]}
        for token in tokens:
            start, end = base + token.bit_start, base + token.bit_end
            if start in breaks:
                self._break_at_end()
            text = self.shown.text(token)
            self.starts.append(start)
            self.ends.append(end)
            self.byte_starts.append(start // 8)
            self.byte_ends.append(max(start // 8 + 1, -(-end // 8)))
            self.texts.append(text)
            self.chars.append(self.chars[-1] + len(text))

    def _break_at_end(self) -> None:
        """End the text so far with a line break, as :func:`_break_before` does,
        and move the characters after it along."""
        broke = _break_before(self.texts)
        if broke < 0:
            return
        for i in range(broke + 1, len(self.chars)):
            self.chars[i] += 1

    def prepend(
        self, start: int, tokens: list[Token], starts: list[int] | tuple[int, ...] = ()
    ) -> bool:
        """Put tokens decoded from byte ``start`` up to the origin in front of
        those kept, when they join: the last ends where the first kept token
        starts, and decoding on from there reads the same. ``True`` when they
        were taken."""
        if not self.resumable or start >= self.origin or not tokens:
            return False
        if start * 8 + tokens[-1].bit_end != self.origin * 8:
            return False
        kept = (self.starts, self.ends, self.byte_starts, self.byte_ends, self.texts)
        self.starts, self.ends, self.byte_starts, self.byte_ends, self.texts = (
            [] for _ in kept
        )
        self.chars = [0]
        self._append(start, tokens, starts)
        for column, rest in zip(
            (self.starts, self.ends, self.byte_starts, self.byte_ends, self.texts),
            kept,
            strict=True,
        ):
            column.extend(rest)
        at = self.chars[-1]
        for text in kept[4]:
            at += len(text)
            self.chars.append(at)
        self.origin = start
        return True

    def above(self, lo: int, offset: int) -> TextModel | None:
        """The kept tokens from the first starting at or after byte ``lo`` to
        those ending by ``offset``: the text above a window there, when the
        tokens reach that far back; ``None`` when they do not."""
        if lo < self.origin or offset > self.end:
            return None
        first = bisect_left(self.starts, lo * 8)
        last = bisect_right(self.ends, offset * 8)
        if last <= first:
            return None
        return self._model(first, last, self.byte_starts[first], offset)

    def model(self, offset: int, stop: int) -> TextModel | None:
        """The tokens from ``offset`` that end by ``stop``, as a model; ``None``
        when they cannot be served from here."""
        first = self._first(offset)
        if first is None or stop > self.end:
            return None
        return self._model(first, bisect_right(self.ends, stop * 8), offset, stop)

    def _model(self, first: int, last: int, offset: int, stop: int) -> TextModel:
        """Tokens ``first`` to ``last`` as the text of bytes ``offset`` to
        ``stop``."""
        base = self.chars[first]
        char_starts = [c - base for c in self.chars[first:last]]
        char_ends = [c - base for c in self.chars[first + 1 : last + 1]]
        byte_starts, byte_ends = (
            self.byte_starts[first:last],
            self.byte_ends[first:last],
        )
        spans = list(zip(char_starts, char_ends, byte_starts, byte_ends, strict=True))
        columns = (char_starts, char_ends, byte_starts, byte_ends)
        body = "".join(self.texts[first:last])
        return TextModel(body, spans, offset, stop - offset, columns)
