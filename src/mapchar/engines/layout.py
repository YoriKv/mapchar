"""Layout: place a string's characters in a text box, and wrap text to fit."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.core.table import TableSet, TokenKind
from mapchar.core.text import units
from mapchar.core.tokens import (
    CodeRef,
    TextRun,
    Token,
    escape_text,
    parse_text,
    plain_text,
)


@dataclass(frozen=True)
class Placement:
    text: str
    """The character drawn here; empty for data that draws a placeholder."""
    x: int
    y: int
    page: int
    token_index: int
    """Which token (or text atom) placed this character."""
    overflow: bool = False
    missing: bool = False
    """The font cannot draw it, or no table entry matched it: a placeholder."""


@dataclass
class Layout:
    placements: list[Placement] = field(default_factory=list)
    pages: int = 1
    overflow_width: bool = False
    overflow_lines: bool = False

    @property
    def overflows(self) -> bool:
        return self.overflow_width or self.overflow_lines


def code_effects(
    table_set: TableSet | None, line_label: str = "line"
) -> dict[str, CodeEffect]:
    """What each code does to layout before a block's box says otherwise.

    Every entry that declares an effect (``41{page}=[next]``) has it — the start
    table's word first where two tables label a code alike — and the block's
    line code is a *newline* unless its table says what it is.
    """
    effects: dict[str, CodeEffect] = {}
    if table_set is not None:
        for table in table_set.tables.values():  # the start table first
            for label, effect in table.effects().items():
                effects.setdefault(label, CodeEffect(effect))
    if line_label:
        effects.setdefault(line_label, CodeEffect(Effect.NEWLINE))
    return effects


def with_code_effects(box: TextBox, defaults: dict[str, CodeEffect]) -> TextBox:
    """``box`` with ``defaults`` (:func:`code_effects`) under its own effects:
    what a block's Codes tab sets for a code wins, *none* included."""
    if not defaults:
        return box
    return replace(box, effects={**defaults, **box.effects})


def effect_of(box: TextBox, label: str, page_label: str | None = None) -> CodeEffect:
    """What the code ``label`` does to the cursor in ``box``.

    A code the box says nothing about does nothing. A caller that knows the
    block's page code passes ``page_label``: a code with that label breaks the
    page whether or not the box lists it.
    """
    effect = box.effects.get(label, CodeEffect())
    if page_label and label == page_label and effect.effect is not Effect.PAGE:
        return CodeEffect(Effect.PAGE)
    return effect


@dataclass(frozen=True)
class _Piece:
    """One drawable unit: a character or a code."""

    text: str
    is_code: bool
    index: int
    """Which token (or parsed item) the piece came from."""
    label: str = ""
    """The code's label, empty for a character or a code that has none."""
    raw: bool = False
    """Data no entry matched: it draws a placeholder."""


def _split_text(text: str, index: int, out: list[_Piece]) -> None:
    """Append ``text`` as one piece per character.

    A character is a grapheme — a base character with the combining marks that
    follow it — so a decomposed dakuten kana is drawn and measured as one.
    """
    for unit in units(text):
        out.append(_Piece(unit, False, index))


def _pieces_from_tokens(tokens: list[Token]) -> list[_Piece]:
    pieces: list[_Piece] = []
    for i, t in enumerate(tokens):
        if t.fallback:
            pieces.append(_Piece(t.text(), True, i))
        elif t.entry is None:
            # Unmatched data is bracketed bytes: its label is what it spells,
            # so a box that names one still reaches it.
            text = t.text()
            pieces.append(_Piece(text, True, i, label=text[1:-1], raw=True))
        elif t.entry.kind is TokenKind.TEXT and t.entry.label is None:
            _split_text(plain_text(t.entry.text), i, pieces)
        else:
            label = t.entry.label
            pieces.append(
                _Piece(f"[{label}]" if label else t.text(), True, i, label=label or "")
            )
    return pieces


def _pieces_from_text(text: str) -> list[_Piece]:
    pieces: list[_Piece] = []
    for i, item in enumerate(parse_text(text)):
        if isinstance(item, TextRun):
            _split_text(item.text, i, pieces)
        else:
            ref: CodeRef = item
            pieces.append(
                _Piece(f"[{ref.label}]", True, i, ref.label, raw=ref.is_raw_byte)
            )
    return pieces


def _pieces(source: list[Token] | str) -> list[_Piece]:
    return (
        _pieces_from_tokens(source)
        if isinstance(source, list)
        else _pieces_from_text(source)
    )


def drawn_text(source: list[Token] | str) -> list[str]:
    """Every character the source draws, in order.

    What a font has to be measured for before it can lay the source out: the
    UI measures these through the real font (:mod:`mapchar.ui.preview_font`)
    and freezes the numbers into a :class:`~mapchar.core.font.Font`.
    """
    return [piece.text for piece in _pieces(source) if not piece.is_code]


def layout(source: list[Token] | str, font: Font, box: TextBox) -> Layout:
    """Place the characters of tokens or script text; nothing is drawn."""
    result = Layout()
    x, line, page = box.origin_x, 0, 0
    max_lines = box.max_lines

    def newline() -> None:
        nonlocal x, line
        x = box.origin_x
        line += 1
        if line >= max_lines:
            result.overflow_lines = True

    def new_page() -> None:
        nonlocal x, line, page
        x = box.origin_x
        line = 0
        page += 1

    for piece in _pieces(source):
        index = piece.index
        if piece.is_code:
            effect = effect_of(box, piece.label)
            if effect.effect is Effect.NEWLINE:
                newline()
                continue
            if effect.effect is Effect.PAGE:
                new_page()
                continue
            if effect.effect is Effect.SPACE:
                x += effect.value
                continue
            if effect.effect is Effect.END:
                break
            if not piece.raw:
                # A code draws nothing: it is an effect, not a character.
                continue
            # Data no entry matched draws a placeholder.
            text, missing = "", True
            advance = font.default_advance
        else:
            text = piece.text
            missing = not font.spells(text)
            advance = font.advance(text)
            if missing and text == " ":
                # A space is never a gap in the font: it advances and draws nothing.
                x += advance + box.letter_spacing
                continue
        # ``x`` runs from ``origin_x`` here, so the right edge is ``width``
        # itself; :func:`wrap` measures from 0 instead.
        too_wide = x + advance > box.width
        over = too_wide or line >= max_lines
        if over:
            result.overflow_width = result.overflow_width or too_wide
        y = box.origin_y + line * box.line_height
        result.placements.append(Placement(text, x, y, page, index, over, missing))
        x += advance + box.letter_spacing
    result.pages = page + 1
    return result


@dataclass
class CharLayout:
    """How script text lays out by characters alone: what a block whose box
    sets ``chars_per_line`` knows of its fit."""

    widest: int = 0
    """Characters on the longest line."""
    lines: int = 1
    """Lines on the fullest page."""
    overflow_width: bool = False
    overflow_lines: bool = False

    @property
    def overflows(self) -> bool:
        return self.overflow_width or self.overflow_lines


def char_count(text: str) -> int:
    """How many character cells a run of plain text takes: one per grapheme."""
    return len(units(text))


def char_layout(text: str, box: TextBox) -> CharLayout:
    """Count the characters of each line and the lines of each page.

    A *newline* code ends the line, a *page* code the page, a *space* code
    takes one cell, an *end* code stops the count, any other code takes
    nothing. Width overflows past ``chars_per_line`` when it is set, lines past
    ``lines_per_page`` when it is.
    """
    result = CharLayout()
    x, lines = 0, 1
    for item in parse_text(text):
        if isinstance(item, CodeRef):
            effect = effect_of(box, item.label).effect
            if effect is Effect.NEWLINE:
                x, lines = 0, lines + 1
                result.lines = max(result.lines, lines)
            elif effect is Effect.PAGE:
                x, lines = 0, 1
            elif effect is Effect.SPACE:
                x += 1
            elif effect is Effect.END:
                break
            else:
                continue
        else:
            x += char_count(item.text)
        result.widest = max(result.widest, x)
    if box.chars_per_line > 0 and result.widest > box.chars_per_line:
        result.overflow_width = True
    if box.lines_per_page > 0 and result.lines > box.lines_per_page:
        result.overflow_lines = True
    return result


def measure(text: str, font: Font, box: TextBox) -> int:
    """Pixel width of a run of plain text through ``font``."""
    parts = units(text)
    width = sum(font.advance(part) + box.letter_spacing for part in parts)
    return max(0, width - box.letter_spacing) if parts else 0


def unspellable(source: list[Token] | str, font: Font) -> list[str]:
    """The distinct characters the font cannot draw, in order.

    Codes are left out: one that draws nothing is an effect, not a gap in the
    font. A space is never reported — it advances and draws nothing.
    """
    out: list[str] = []
    for piece in _pieces(source):
        if piece.is_code or piece.text == " ":
            continue
        if not font.spells(piece.text) and piece.text not in out:
            out.append(piece.text)
    return out


def wrap(
    text: str,
    font: Font | None,
    box: TextBox,
    newline_label: str,
    page_label: str | None = None,
) -> tuple[str, bool]:
    """Re-break script text to fit the box; returns the text and whether it overflows.

    Existing newline codes go, except those right after a page code; words
    are laid out greedily; a word wider than the box breaks at a character.
    With a font, widths are pixels through it; without one, the box's
    ``chars_per_line`` is the width and every character is one cell.
    """
    items = parse_text(text)
    # Drop existing newline codes, except one right after a page code: a page
    # that opens on its second line says so with that newline.
    kept: list[TextRun | CodeRef] = []
    after_page = False
    for item in items:
        is_code = isinstance(item, CodeRef)
        if (
            is_code
            and item.label == newline_label
            and not item.words
            and not after_page
        ):
            continue
        after_page = bool(page_label) and is_code and item.label == page_label
        kept.append(item)
    out: list[str] = []
    x = 0
    line = 0
    overflow = False
    if font is not None:
        measured = font  # bound here, so the closure below is never handed None
        limit = box.width - box.origin_x
        max_lines = box.max_lines
        spacing = box.letter_spacing
        space_width = measured.advance(" ")

        def width_of(word: str) -> int:
            return measure(word, measured, box)

    else:
        limit = box.chars_per_line
        max_lines = box.lines_per_page or 0
        spacing = 0
        space_width = 1

        def width_of(word: str) -> int:
            return char_count(word)

    def pieces_of(word: str) -> list[str]:
        return units(word)

    def emit_newline() -> None:
        nonlocal x, line, overflow
        line += 1
        x = 0
        if max_lines and line >= max_lines:
            if page_label:
                # A page starts at its own first line: no newline goes with it.
                out.append(f"[{page_label}]")
                line = 0
                return
            overflow = True
        out.append(f"[{newline_label}]")

    def place_word(word: str) -> None:
        nonlocal x
        w = width_of(word)
        if x and x + w > limit:
            emit_newline()
        if w > limit:
            for piece in pieces_of(word):
                cw = width_of(piece)
                if x and x + cw > limit:
                    emit_newline()
                out.append(escape_text(piece))
                x += cw + spacing
            return
        out.append(escape_text(word))
        x += w + spacing

    for item in kept:
        if isinstance(item, CodeRef):
            words = " ".join(item.words)
            out.append(f"[{item.label}{' ' + words if words else ''}]")
            effect = effect_of(box, item.label, page_label)
            if effect.effect is Effect.PAGE:
                x, line = 0, 0
            elif effect.effect is Effect.NEWLINE:
                x, line = 0, line + 1
            elif effect.effect is Effect.SPACE:
                x += effect.value if font is not None else 1
            continue
        parts = item.text.split(" ")
        for i, word in enumerate(parts):
            if i:
                w = width_of(word)
                if x and x + space_width + spacing + w > limit:
                    emit_newline()
                elif x:
                    out.append(" ")
                    x += space_width + spacing
            if word:
                place_word(word)
    result = "".join(out)
    # A newline the wrap put right before a page code is redundant.
    if page_label:
        result = result.replace(f"[{newline_label}][{page_label}]", f"[{page_label}]")
    return result, overflow
