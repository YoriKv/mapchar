from __future__ import annotations

import string
import unicodedata
from dataclasses import replace

from helpers import ASCII_TABLE, table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.engines.layout import char_layout, layout, measure, unspellable, wrap
from mapchar.pipeline.extract import extract

FONT = Font(
    "Test",
    16,
    height=8,
    ascent=6,
    advances={" ": 4, **{c: 6 for c in string.ascii_uppercase}},
    default_advance=8,
    missing=frozenset(string.ascii_lowercase),
)
"""A measured font as the UI hands one over: capitals six pixels wide, a
four-pixel space, and no lower case at all."""
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
    placed = [(p.text, p.x, p.y) for p in result.placements]
    assert placed[:3] == [("A", 0, 0), ("B", 6, 0), (" ", 12, 0)]
    assert any(p.overflow for p in result.placements)  # CDEF runs past 32 px
    assert result.overflow_width and not result.overflow_lines
    assert [p for p in result.placements if p.y == 8][0].text == "G"  # G on line 2
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


def test_a_space_the_font_cannot_draw_is_never_a_gap():
    """A space advances and places nothing, drawable or not."""
    font = replace(FONT, missing=frozenset(" "))
    result = layout("A B", font, BOX)
    assert [(p.text, p.x) for p in result.placements] == [("A", 0), ("B", 10)]


def test_unmatched_bytes_draw_a_placeholder():
    placed = [(p.text, p.missing, p.x) for p in layout("A[$FF]B", FONT, BOX).placements]
    assert placed == [("A", False, 0), ("", True, 6), ("B", False, 14)]
    ts = table_set(ASCII_TABLE, "main")
    ex = extract(b"A\xff\x00", BlockConfig(RangeSource(0, 3), EndToken(), "main"), ts)
    tokens = ex.strings[0].original
    assert [p.missing for p in layout(tokens, FONT, BOX).placements] == [False, True]


def test_text_the_font_cannot_draw_is_placed_and_marked():
    """It still takes room — only a box is drawn where the character was."""
    placed = [(p.text, p.missing) for p in layout("Aq", FONT, BOX).placements]
    assert placed == [("A", False), ("q", True)]


def test_a_code_draws_nothing():
    ts = table_set(ASCII_TABLE + "FD=[icon]\n", "main")
    ex = extract(b"A\xfdB\x00", BlockConfig(RangeSource(0, 4), EndToken(), "main"), ts)
    tokens = ex.strings[0].original
    assert [p.text for p in layout(tokens, FONT, BOX).placements] == ["A", "B"]


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


def test_wrap_measures_space_effects():
    box = replace(BOX, effects={**BOX.effects, "pad": CodeEffect(Effect.SPACE, 20)})
    assert wrap("AB[pad]CD", FONT, box, "line")[0] == "AB[pad][line]CD"


KANA_FONT = Font(
    "Test",
    16,
    height=8,
    ascent=6,
    advances={c: 8 for c in "あいうえおぎ"},
    default_advance=8,
    missing=frozenset("が"),
)


def test_a_decomposed_kana_is_one_character():
    decomposed = unicodedata.normalize("NFD", "い")  # い has no mark of its own
    assert decomposed == "い"
    text = unicodedata.normalize("NFD", "あい")
    result = layout(text, KANA_FONT, TextBox(width=64, height=8, line_height=8))
    assert [p.text for p in result.placements] == ["あ", "い"]
    assert [p.x for p in result.placements] == [0, 8]


def test_a_decomposed_kana_is_reported_whole():
    # However the text arrived, a kana the font cannot draw is one character
    # missing, not a base character and a mark.
    assert unspellable(unicodedata.normalize("NFD", "が"), KANA_FONT) == ["が"]


CHAR_BOX = replace(BOX, chars_per_line=5, lines_per_page=2)
"""A box that counts: five characters a line, two lines a page."""


def test_char_layout_counts_characters_and_lines():
    fit = char_layout("ABCDE[line]FG[end]", CHAR_BOX)
    assert (fit.widest, fit.lines) == (5, 2) and not fit.overflows
    over = char_layout("ABCDEF", CHAR_BOX)
    assert over.overflow_width and not over.overflow_lines
    tall = char_layout("A[line]B[line]C", CHAR_BOX)
    assert tall.overflow_lines and tall.lines == 3
    # A page code starts the count over; a space code takes a cell; a
    # decomposed kana is one character; an end code stops the count.
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
