"""Layout: place a string's glyphs in a text box, and wrap text to fit."""

from __future__ import annotations

from dataclasses import dataclass, field

from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.core.table import EntryKind
from mapchar.core.tokens import CodeRef, TextRun, Token, parse_text, plain_text


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


def _pieces_from_tokens(tokens: list[Token]) -> list[tuple[str, bool, int]]:
    """``(text, is_code, token_index)`` per drawable unit."""
    pieces = []
    for i, t in enumerate(tokens):
        if t.fallback or t.entry is None:
            pieces.append((t.text(), True, i))
        elif t.entry.kind is EntryKind.TEXT and t.entry.label is None:
            for ch in plain_text(t.entry.text):
                pieces.append((ch, False, i))
        else:
            label = t.entry.label
            pieces.append((f"[{label}]" if label else t.text(), True, i))
    return pieces


def _pieces_from_text(text: str) -> list[tuple[str, bool, int]]:
    pieces = []
    for i, item in enumerate(parse_text(text)):
        if isinstance(item, TextRun):
            for ch in item.text:
                pieces.append((ch, False, i))
        else:
            ref: CodeRef = item
            pieces.append((f"[{ref.label}]", True, i))
    return pieces


def layout(source: list[Token] | str, font: Font, box: TextBox) -> Layout:
    """Place glyphs for tokens or script text; nothing is drawn."""
    pieces = (
        _pieces_from_tokens(source)
        if isinstance(source, list)
        else _pieces_from_text(source)
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

    for text, is_code, index in pieces:
        if is_code:
            effect = box.effects.get(text[1:-1], CodeEffect())
            glyph = font.glyphs.get(text)
            if glyph is None and effect.effect is Effect.GLYPH:
                glyph = effect.value
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
                continue
            advance = font.advance(glyph)
        else:
            glyph = font.glyph_for(text)
            if text == " ":
                advance = font.space if font.space is not None else font.advance(glyph)
            else:
                advance = font.advance(glyph)
                if glyph is None:
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
    """Pixel width of a run of plain text."""
    width = 0
    for ch in text:
        glyph = font.glyph_for(ch)
        if ch == " " and font.space is not None:
            width += font.space + box.letter_spacing
        else:
            width += font.advance(glyph) + box.letter_spacing
    return max(0, width - box.letter_spacing) if text else 0


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
    # Drop existing newline codes, but keep page codes and everything else.
    kept: list[TextRun | CodeRef] = []
    for item in items:
        if isinstance(item, CodeRef) and item.label == newline_label and not item.words:
            continue
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
                out.append(f"[{page_label}]")
                line = 0
            else:
                overflow = True
        out.append(f"[{newline_label}]")

    def place_word(word: str) -> None:
        nonlocal x
        w = measure(word, font, box)
        if x and x + w > limit:
            emit_newline()
        if w > limit:
            for ch in word:
                cw = measure(ch, font, box)
                if x and x + cw > limit:
                    emit_newline()
                from mapchar.core.tokens import escape_text

                out.append(escape_text(ch))
                x += cw + box.letter_spacing
            return
        from mapchar.core.tokens import escape_text

        out.append(escape_text(word))
        x += w + box.letter_spacing

    for item in kept:
        if isinstance(item, CodeRef):
            words = " ".join(item.words)
            out.append(f"[{item.label}{' ' + words if words else ''}]")
            if page_label and item.label == page_label:
                x, line = 0, 0
            continue
        parts = item.text.split(" ")
        for i, word in enumerate(parts):
            if i:
                space = (
                    font.space
                    if font.space is not None
                    else font.advance(font.glyph_for(" "))
                )
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
