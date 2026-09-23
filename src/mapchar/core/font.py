"""Fonts and text boxes for the preview: frozen values, no drawing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mapchar.core.text import nfc


@dataclass(frozen=True)
class Font:
    """A system font as the preview has measured it.

    Measuring a real font is Qt's work and so the UI's
    (:mod:`mapchar.ui.preview_font`); what layout needs is frozen here — how
    tall a line stands, where its baseline sits, how far each character
    advances, and which characters the font cannot draw.
    """

    family: str = ""
    size: int = 16
    """Point size the family was measured at."""
    height: int = 16
    """Pixels a line of it occupies."""
    ascent: int = 12
    """Pixels from the top of a line down to the baseline."""
    advances: dict[str, int] = field(default_factory=dict)
    """Pixels each character advances, by grapheme."""
    default_advance: int = 8
    """Advance of a character nobody measured."""
    missing: frozenset[str] = frozenset()
    """Characters the family has no glyph for."""

    def __post_init__(self) -> None:
        # Measured under NFC, so text decoded from a table is found however the
        # table spelled it.
        composed = {nfc(t): w for t, w in self.advances.items()}
        if composed != self.advances:
            object.__setattr__(self, "advances", composed)
        absent = frozenset(nfc(t) for t in self.missing)
        if absent != self.missing:
            object.__setattr__(self, "missing", absent)

    def advance(self, text: str) -> int:
        return self.advances.get(nfc(text), self.default_advance)

    def spells(self, text: str) -> bool:
        """Whether the family can draw ``text``."""
        return nfc(text) not in self.missing


class Effect(Enum):
    NONE = "none"
    NEWLINE = "newline"
    PAGE = "page"
    PAUSE = "pause"
    SPACE = "space"
    END = "end"


@dataclass(frozen=True)
class CodeEffect:
    effect: Effect = Effect.NONE
    value: int = 0
    """Pixels, for SPACE."""


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
    chars_per_line: int = 0
    """How many characters a line holds; 0 sets no limit. With it, the
    preview and Wrap work by counting characters rather than measuring them."""

    @property
    def max_lines(self) -> int:
        if self.lines_per_page > 0:
            return self.lines_per_page
        return max(1, (self.height - self.origin_y) // max(self.line_height, 1))
