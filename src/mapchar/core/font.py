"""Fonts and text boxes for the preview: frozen values, no drawing."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


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
    """Characters laid over consecutive glyphs from ``base``."""
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

    def glyph_for(self, text: str) -> int | None:
        if text in self.glyphs:
            return self.glyphs[text]
        i = self.chars.find(text) if len(text) == 1 else -1
        if i >= 0:
            return self.base + i
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

    @property
    def max_lines(self) -> int:
        if self.lines_per_page > 0:
            return self.lines_per_page
        return max(1, (self.height - self.origin_y) // max(self.line_height, 1))
