from __future__ import annotations

from helpers import table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.engines.layout import layout, measure, wrap
from mapchar.pipeline.extract import extract

FONT = Font(None, 8, 8, 16, 0, " ABCDEFGHIJKLMNOPQRSTUVWXYZ", widths=(4,) + (6,) * 26)
BOX = TextBox(
    width=32,
    height=16,
    line_height=8,
    effects={
        "line": CodeEffect(Effect.NEWLINE),
        "page": CodeEffect(Effect.PAGE),
        "end": CodeEffect(Effect.END),
    },
)


def test_layout_places_and_flags_overflow():
    ts = table_set("@table main\n@charset ascii\n/00=[end]\nFE=[line]\\n\n", "main")
    ex = extract(
        b"AB CDEF\xfeGH\x00", BlockConfig(RangeSource(0, 11), EndToken(), "main"), ts
    )
    tokens = ex.strings[0].original
    result = layout(tokens, FONT, BOX)
    xs = [(p.glyph, p.x, p.y) for p in result.placements]
    assert xs[:3] == [(1, 0, 0), (2, 6, 0), (0, 12, 0)]
    assert any(p.overflow for p in result.placements)  # CDEF runs past 32 px
    assert result.overflow_width and not result.overflow_lines
    assert [p for p in result.placements if p.y == 8][0].glyph == 7  # G on line 2
    result = layout("A[line]B[line]C", FONT, BOX)
    assert result.overflow_lines


def test_measure_and_wrap():
    assert measure("AB", FONT, BOX) == 12 and measure("A B", FONT, BOX) == 16
    text, over = wrap("AB CD EF GH", FONT, BOX, "line")
    assert text == "AB CD[line]EF GH" and not over
    text, over = wrap("AB CD EF GH IJ", FONT, BOX, "line")
    assert over and text.count("[line]") == 2
    text, over = wrap("AB CD EF GH IJ", FONT, BOX, "line", "page")
    assert not over and "[page]" in text and "[line][page]" not in text
    text, _ = wrap("ABCDEFG", FONT, BOX, "line")
    assert text == "ABCDE[line]FG"
    text, _ = wrap("A[line]B[color $03]C", FONT, BOX, "line")
    assert text == "AB[color $03]C"
