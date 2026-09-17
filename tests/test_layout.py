from __future__ import annotations

import unicodedata
from dataclasses import replace

from helpers import ASCII_TABLE, table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.engines.layout import char_layout, layout, measure, unspellable, wrap
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
    ts = table_set(ASCII_TABLE + "FE=[line]\\n\n", "main")
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


def test_space_without_a_glyph_is_never_a_gap():
    """A space takes the font's space width and places nothing to tint."""
    font = Font(None, 8, 8, 16, 1, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", space=4, missing=99)
    result = layout("A B", font, BOX)
    assert [(p.glyph, p.x) for p in result.placements] == [(1, 0), (2, 12)]


def test_unmatched_bytes_draw_the_missing_glyph():
    font = replace(FONT, missing=99)
    assert [p.glyph for p in layout("A[$FF]B", font, BOX).placements] == [1, 99, 2]
    ts = table_set(ASCII_TABLE, "main")
    ex = extract(b"A\xff\x00", BlockConfig(RangeSource(0, 3), EndToken(), "main"), ts)
    tokens = ex.strings[0].original
    assert [p.glyph for p in layout(tokens, font, BOX).placements] == [1, 99]


def test_multi_character_override_beats_single_characters():
    font = replace(FONT, glyphs={"TH": 40})
    assert [p.glyph for p in layout("THE", font, BOX).placements] == [40, 5]
    assert measure("THE", font, BOX) == 14
    # The same inside one multi-character text entry's token.
    ts = table_set(ASCII_TABLE + "FD=THE\n", "main")
    ex = extract(b"\xfd\x00", BlockConfig(RangeSource(0, 2), EndToken(), "main"), ts)
    tokens = ex.strings[0].original
    assert [p.glyph for p in layout(tokens, font, BOX).placements] == [40, 5]


def test_unspellable_lists_what_the_font_cannot_draw():
    assert unspellable("AB q z q", FONT) == ["q", "z"]
    assert unspellable("A[line]B", FONT) == []


def test_wrap_page_and_newline_in_either_order():
    # The page the wrap inserts starts on its own first line: no [page][line].
    text, over = wrap("AB CD EF GH IJ", FONT, BOX, "line", "page")
    assert text == "AB CD[line]EF GH[page]IJ" and not over
    assert "[line][page]" not in text and "[page][line]" not in text
    # A newline before a page code is redundant and goes.
    assert wrap("AB[line][page]CD", FONT, BOX, "line", "page")[0] == "AB[page]CD"
    # One right after a page code is the page's own blank first line: it stays.
    assert wrap("AB[page][line]CD", FONT, BOX, "line", "page")[0] == "AB[page][line]CD"


def test_wrap_measures_space_and_glyph_effects():
    box = replace(
        BOX,
        effects={
            **BOX.effects,
            "pad": CodeEffect(Effect.SPACE, 20),
            "icon": CodeEffect(Effect.GLYPH, 5),
        },
    )
    assert wrap("AB[pad]CD", FONT, box, "line")[0] == "AB[pad][line]CD"
    assert wrap("AB[icon]CDE", FONT, box, "line")[0] == "AB[icon][line]CDE"


KANA_FONT = Font(
    None,
    8,
    8,
    16,
    0x40,
    "あいうえお",
    glyphs={"[line]": 0x7F, "がぎ": 0x50},
    widths=(8,) * 0x60,
)


def test_a_decomposed_kana_takes_one_glyph_slot():
    decomposed = unicodedata.normalize("NFD", "い")  # い has no mark of its own
    assert decomposed == "い"
    text = unicodedata.normalize("NFD", "あい")
    result = layout(text, KANA_FONT, TextBox(width=64, height=8, line_height=8))
    assert [p.glyph for p in result.placements] == [0x40, 0x41]
    assert [p.x for p in result.placements] == [0, 8]


def test_a_multi_character_override_wins_over_its_first_kana():
    # A font may draw two kana in one cell; the override is matched whole,
    # whatever form the text arrived in.
    text = unicodedata.normalize("NFD", "がぎあ")
    result = layout(text, KANA_FONT, TextBox(width=64, height=8, line_height=8))
    assert [p.glyph for p in result.placements] == [0x50, 0x40]
    assert unspellable(unicodedata.normalize("NFD", "が"), KANA_FONT) == ["が"]


CHAR_BOX = replace(BOX, chars_per_line=5, lines_per_page=2)
"""A box for a block with no font: five characters a line, two lines a page."""


def test_char_layout_counts_characters_and_lines():
    fit = char_layout("ABCDE[line]FG[end]", CHAR_BOX)
    assert (fit.widest, fit.lines) == (5, 2) and not fit.overflows
    over = char_layout("ABCDEF", CHAR_BOX)
    assert over.overflow_width and not over.overflow_lines
    tall = char_layout("A[line]B[line]C", CHAR_BOX)
    assert tall.overflow_lines and tall.lines == 3
    # A page code starts the count over; a space or glyph code takes a cell;
    # a decomposed kana is one character; an end code stops the count.
    paged = char_layout("A[line]B[page]C[line]D", CHAR_BOX)
    assert paged.lines == 2 and not paged.overflows
    effects = {**CHAR_BOX.effects, "sp": CodeEffect(Effect.SPACE, 4)}
    cells = replace(CHAR_BOX, effects=effects)
    assert char_layout("AB[sp]CD", cells).widest == 5
    assert char_layout(unicodedata.normalize("NFD", "がぎ"), CHAR_BOX).widest == 2
    wide = replace(CHAR_BOX, chars_per_line=9)
    assert char_layout("ABCDEFG[end]HIJ", wide).widest == 7
    # Nothing set, nothing overflows.
    assert not char_layout("ABCDEFGHIJ", BOX).overflows


def test_wrap_without_a_font_counts_characters():
    text, over = wrap("AB CD EF GH", None, CHAR_BOX, "line")
    assert text == "AB CD[line]EF GH" and not over
    text, over = wrap("AB CD EF GH IJ", None, CHAR_BOX, "line")
    assert over and text.count("[line]") == 2
    text, over = wrap("AB CD EF GH IJ", None, CHAR_BOX, "line", "page")
    assert not over and "[page]" in text
    # A word longer than the line breaks at a character.
    text, _ = wrap("ABCDEFG", None, CHAR_BOX, "line")
    assert text == "ABCDE[line]FG"
    # No line limit: a page is never forced and nothing overflows by lines.
    unbounded = replace(CHAR_BOX, lines_per_page=0)
    text, over = wrap("AB CD EF GH IJ KL", None, unbounded, "line")
    assert not over and text.count("[line]") == 2
