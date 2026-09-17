"""Fonts and text boxes for the preview: frozen values, no drawing."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from mapchar.core.text import char_units, nfc


@dataclass(frozen=True)
class Font:
    """A glyph sheet image cut into cells, plus which glyph each text draws."""

    path: str | None
    cell_width: int = 8
    cell_height: int = 8
    columns: int = 16
    base: int = 0
    """Glyph index of the first character of ``chars``."""
    chars: str = ""
    """Characters laid over consecutive glyphs from ``base``, one glyph each.

    A character is a grapheme: a base plus the combining marks that follow it,
    so a decomposed dakuten kana takes one glyph slot, not two."""
    glyphs: dict[str, int] = field(default_factory=dict)
    """Explicit token text (or ``[label]``) to glyph index."""
    widths: tuple[int, ...] = ()
    """Advance width per glyph index; empty means the cell width."""
    space: int | None = None
    """Advance of a space; defaults to the glyph's width or the cell width."""
    missing: int | None = None
    """Glyph drawn for text with no glyph; None draws a box."""
    transparent: int | None = 0
    """Palette index or None; RGB sheets use the top-left pixel's colour."""

    def __post_init__(self) -> None:
        # The alphabet and the overrides are NFC, so text decoded from a table
        # finds its glyph however the font's characters were typed.
        object.__setattr__(self, "chars", nfc(self.chars))
        composed = {nfc(t): g for t, g in self.glyphs.items()}
        if composed != self.glyphs:
            object.__setattr__(self, "glyphs", composed)

    @property
    def units(self) -> tuple[str, ...]:
        """``chars`` as one string per glyph slot from ``base``."""
        return char_units(self.chars)

    def glyph_for(self, text: str) -> int | None:
        composed = nfc(text)
        if composed in self.glyphs:
            return self.glyphs[composed]
        units = self.units
        if composed in units:
            return self.base + units.index(composed)
        return None

    def advance(self, glyph: int | None) -> int:
        if glyph is None:
            return self.cell_width
        if glyph < len(self.widths):
            return self.widths[glyph]
        return self.cell_width

    def with_widths(self, widths: tuple[int, ...]) -> Font:
        return replace(self, widths=widths)


class Effect(Enum):
    NONE = "none"
    NEWLINE = "newline"
    PAGE = "page"
    SPACE = "space"
    GLYPH = "glyph"
    END = "end"


@dataclass(frozen=True)
class CodeEffect:
    effect: Effect = Effect.NONE
    value: int = 0
    """Pixels for SPACE, a glyph index for GLYPH."""


@dataclass(frozen=True)
class TextBox:
    width: int = 128
    height: int = 32
    line_height: int = 8
    letter_spacing: int = 0
    lines_per_page: int = 0
    """0 means as many as ``height`` holds."""
    origin_x: int = 0
    origin_y: int = 0
    effects: dict[str, CodeEffect] = field(default_factory=dict)
    """Code label to its layout effect."""
    font_index: int | None = None
    """Index of the font entry in the project's entries, when bound."""
    chars_per_line: int = 0
    """How many characters a line holds, for a block with no font; 0 sets no
    limit. With it, *overflows box* and Wrap work by counting characters."""

    @property
    def max_lines(self) -> int:
        if self.lines_per_page > 0:
            return self.lines_per_page
        return max(1, (self.height - self.origin_y) // max(self.line_height, 1))
