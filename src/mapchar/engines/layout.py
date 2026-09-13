"""Layout: place a string's glyphs in a text box, and wrap text to fit."""

from __future__ import annotations

from dataclasses import dataclass, field

from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.core.table import TokenKind
from mapchar.core.text import char_units, graphemes, nfc
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
    glyph: int | None
    x: int
    y: int
    page: int
    token_index: int
    """Which token (or text atom) placed this glyph."""
    overflow: bool = False


@dataclass
class Layout:
    placements: list[Placement] = field(default_factory=list)
    pages: int = 1
    overflow_width: bool = False
    overflow_lines: bool = False

    @property
    def overflows(self) -> bool:
        return self.overflow_width or self.overflow_lines


def glyph_advance(font: Font, text: str) -> int:
    """How far one drawable piece moves the pen, before letter spacing.

    A space takes ``font.space`` when the font sets one, else its glyph's
    width like anything else.
    """
    if text == " " and font.space is not None:
        return font.space
    return font.advance(font.glyph_for(text))


def code_cell(font: Font, box: TextBox, label: str) -> tuple[CodeEffect, int | None]:
    """A code's layout effect and the glyph it draws, if any."""
    effect = box.effects.get(label, CodeEffect())
    glyph = font.glyphs.get(f"[{label}]")
    if glyph is None and effect.effect is Effect.GLYPH:
        glyph = effect.value
    return effect, glyph


@dataclass(frozen=True)
class _Piece:
    """One drawable unit: a character, a multi-character override or a code."""

    text: str
    is_code: bool
    index: int
    """Which token (or parsed item) the piece came from."""
    raw: bool = False
    """Data no entry matched: it draws the missing glyph."""


def overrides_of(font: Font) -> tuple[str, ...]:
    """The font's multi-character text overrides, longest first.

    A character is a grapheme here too: an override spelling a dakuten kana in
    one glyph is the font's own business, not an override of two.
    """
    return tuple(
        sorted(
            (
                t
                for t in font.glyphs
                if len(char_units(t)) > 1 and not t.startswith("[")
            ),
            key=len,
            reverse=True,
        )
    )


def _split_text(
    text: str, overrides: tuple[str, ...], index: int, out: list[_Piece]
) -> None:
    """Append ``text`` as pieces: the longest override first, else one glyph.

    One glyph is one grapheme — a base character with the combining marks that
    follow it — so a decomposed dakuten kana takes a single glyph slot.
    """
    units = graphemes(nfc(text))
    at = 0
    while at < len(units):
        rest = "".join(units[at:])
        for key in overrides:
            if rest.startswith(key):
                out.append(_Piece(key, False, index))
                taken, used = 0, 0
                while taken < len(key) and at + used < len(units):
                    taken += len(units[at + used])
                    used += 1
                at += max(1, used)
                break
        else:
            out.append(_Piece(units[at], False, index))
            at += 1


def _pieces_from_tokens(tokens: list[Token], font: Font) -> list[_Piece]:
    pieces: list[_Piece] = []
    overrides = overrides_of(font)
    for i, t in enumerate(tokens):
        if t.fallback:
            pieces.append(_Piece(t.text(), True, i))
        elif t.entry is None:
            pieces.append(_Piece(t.text(), True, i, raw=True))
        elif t.entry.kind is TokenKind.TEXT and t.entry.label is None:
            _split_text(plain_text(t.entry.text), overrides, i, pieces)
        else:
            label = t.entry.label
            pieces.append(_Piece(f"[{label}]" if label else t.text(), True, i))
    return pieces


def _pieces_from_text(text: str, font: Font) -> list[_Piece]:
    pieces: list[_Piece] = []
    overrides = overrides_of(font)
    for i, item in enumerate(parse_text(text)):
        if isinstance(item, TextRun):
            _split_text(item.text, overrides, i, pieces)
        else:
            ref: CodeRef = item
            pieces.append(_Piece(f"[{ref.label}]", True, i, raw=ref.is_raw_byte))
    return pieces


def layout(source: list[Token] | str, font: Font, box: TextBox) -> Layout:
    """Place glyphs for tokens or script text; nothing is drawn."""
    pieces = (
        _pieces_from_tokens(source, font)
        if isinstance(source, list)
        else _pieces_from_text(source, font)
    )
    result = Layout()
    x, line, page = box.origin_x, 0, 0
    max_lines = box.max_lines

    def newline() -> None:
        nonlocal x, line, page
        x = box.origin_x
        line += 1
        if line >= max_lines:
            result.overflow_lines = True

    def new_page() -> None:
        nonlocal x, line, page
        x = box.origin_x
        line = 0
        page += 1

    for piece in pieces:
        index = piece.index
        if piece.is_code:
            effect, glyph = code_cell(font, box, piece.text[1:-1])
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
            if glyph is None:
                if not piece.raw:
                    continue
                # Data no entry matched draws the missing glyph.
                glyph = font.missing
            advance = font.advance(glyph)
        else:
            glyph = font.glyph_for(piece.text)
            advance = glyph_advance(font, piece.text)
            if glyph is None:
                if piece.text == " ":
                    # A space is the font's space width, never an error.
                    x += advance + box.letter_spacing
                    continue
                glyph = font.missing
        over = (
            x + advance > box.origin_x + (box.width - box.origin_x) or line >= max_lines
        )
        if over:
            result.overflow_width = result.overflow_width or x + advance > box.width
        y = box.origin_y + line * box.line_height
        result.placements.append(Placement(glyph, x, y, page, index, over))
        x += advance + box.letter_spacing
    result.pages = page + 1
    return result


def measure(text: str, font: Font, box: TextBox) -> int:
    """Pixel width of a run of plain text, the font's overrides included."""
    pieces: list[_Piece] = []
    _split_text(text, overrides_of(font), 0, pieces)
    width = 0
    for piece in pieces:
        width += glyph_advance(font, piece.text) + box.letter_spacing
    return max(0, width - box.letter_spacing) if pieces else 0


def unspellable(source: list[Token] | str, font: Font) -> list[str]:
    """The distinct text pieces the font has no glyph for, in order.

    Codes are left out: one that draws nothing is an effect, not a gap in the
    font. A space is never reported — it is the font's space width.
    """
    pieces = (
        _pieces_from_tokens(source, font)
        if isinstance(source, list)
        else _pieces_from_text(source, font)
    )
    out: list[str] = []
    for piece in pieces:
        if piece.is_code or piece.text == " ":
            continue
        if font.glyph_for(piece.text) is None and piece.text not in out:
            out.append(piece.text)
    return out


def wrap(
    text: str,
    font: Font,
    box: TextBox,
    newline_label: str,
    page_label: str | None = None,
) -> tuple[str, bool]:
    """Re-break script text to fit the box; returns the text and whether it overflows.

    Existing newline codes go, except those right after a page code; words
    are laid out greedily; a word wider than the box breaks at a glyph.
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
    limit = box.width - box.origin_x
    overflow = False

    def emit_newline() -> None:
        nonlocal x, line, overflow
        line += 1
        x = 0
        if line >= box.max_lines:
            if page_label:
                # A page starts at its own first line: no newline goes with it.
                out.append(f"[{page_label}]")
                line = 0
                return
            overflow = True
        out.append(f"[{newline_label}]")

    def place_word(word: str) -> None:
        nonlocal x
        w = measure(word, font, box)
        if x and x + w > limit:
            emit_newline()
        if w > limit:
            pieces: list[_Piece] = []
            _split_text(word, overrides_of(font), 0, pieces)
            for piece in pieces:
                cw = measure(piece.text, font, box)
                if x and x + cw > limit:
                    emit_newline()
                out.append(escape_text(piece.text))
                x += cw + box.letter_spacing
            return
        out.append(escape_text(word))
        x += w + box.letter_spacing

    for item in kept:
        if isinstance(item, CodeRef):
            words = " ".join(item.words)
            out.append(f"[{item.label}{' ' + words if words else ''}]")
            effect, glyph = code_cell(font, box, item.label)
            if effect.effect is Effect.PAGE or item.label == page_label:
                x, line = 0, 0
            elif effect.effect is Effect.NEWLINE:
                x, line = 0, line + 1
            elif effect.effect is Effect.SPACE:
                x += effect.value
            elif glyph is not None:
                x += font.advance(glyph) + box.letter_spacing
            continue
        parts = item.text.split(" ")
        for i, word in enumerate(parts):
            if i:
                space = glyph_advance(font, " ")
                w = measure(word, font, box)
                if x and x + space + box.letter_spacing + w > limit:
                    emit_newline()
                elif x:
                    out.append(" ")
                    x += space + box.letter_spacing
            if word:
                place_word(word)
    result = "".join(out)
    # A newline the wrap put right before a page code is redundant.
    if page_label:
        result = result.replace(f"[{newline_label}][{page_label}]", f"[{page_label}]")
    return result, overflow
