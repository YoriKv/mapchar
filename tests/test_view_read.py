"""Reading the bytes in view: string rules in step with the block, pointers,
the standard encodings as tables, a new block's region, and the line breaks the
Text tab puts between strings the tokens do not end themselves."""

from __future__ import annotations

from dataclasses import replace

from helpers import ABC_TABLE, table_set
from mapchar.core.bits import Bits
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    with_region,
)
from mapchar.core.table import TableSet, TokenKind
from mapchar.core.tokens import render
from mapchar.pipeline.extract import extract
from mapchar.pipeline.text_view import TextDecode, text_model
from mapchar.pipeline.view_read import (
    align_before,
    cuts_at_end_tokens,
    decode_strings,
    pointer_cells,
    target_string,
)
from mapchar.plugins.charsets import CharsetTables
from mapchar.plugins.registry import default_registry


def _ascii() -> TableSet:
    tables = CharsetTables(default_registry())
    return TableSet.build(tables["ascii"], tables)


def test_the_encodings_are_tables_built_when_first_read():
    tables = CharsetTables(default_registry())
    ids = [cid for cid, _ in tables.names()]
    assert ids[:2] == ["ascii", "latin-1"]
    assert {"shift-jis", "jis-x-0201", "utf-8", "utf-16le", "gbk"} <= set(ids)
    assert "none" not in tables and "none" not in ids
    assert not tables._built  # listing them reads none
    ascii_table = tables["ascii"]
    assert ascii_table.entries["01000001"].text == "A"
    # A string of an encoding ends at its NUL, as wide as its code unit.
    assert ascii_table.entries["00000000"].kind is TokenKind.END
    assert tables["utf-16le"].entries["0" * 16].kind is TokenKind.END
    assert tables["ascii"] is ascii_table


def test_half_width_katakana_are_single_bytes_in_jis_x_0201():
    table = CharsetTables(default_registry())["jis-x-0201"]
    assert table.entries[format(0xB1, "08b")].text == "ｱ"
    assert max(len(bits) for bits in table.entries) == 8


def test_a_view_cuts_strings_by_the_reading_s_string_type():
    data = b"ABCDEF"
    ends = decode_strings(data, None, _ascii())
    assert ends.starts == [0]
    fixed = BlockConfig(RangeSource(0, 0), FixedLength(2), "ascii")
    run = decode_strings(data, fixed, _ascii())
    assert run.starts == [0, 16, 32]
    assert render(run.tokens) == "ABCDEF"


def test_a_view_cuts_fixed_strings_in_step_with_the_block():
    data = b"ABCDEF"
    fixed = BlockConfig(RangeSource(0, 0), FixedLength(2), "ascii")
    # A view a byte in shows the rest of the string it starts inside, and
    # whole ones after that.
    run = decode_strings(data[1:], fixed, _ascii(), 1)
    assert run.starts == [0, 8, 24]
    assert render(run.tokens) == "BCDEF"


def test_a_view_steps_over_a_record_header_as_the_block_does():
    """The header bytes are no string's: the view passes over them and shows
    them as the bytes they are, so its strings start where the block's do."""
    abc = table_set(ABC_TABLE, "main")
    data = bytes.fromhex("05 CB 02 41 42  06 CB 01 42  07 CB 02 42 41")
    pascal = BlockConfig(RangeSource(0, len(data)), Pascal(1), "main", header=2)
    run = decode_strings(data, pascal, abc)
    assert render(run.tokens) == "[$05][$CB]AB[$06][$CB]B[$07][$CB]BA"
    assert run.starts == [16, 56, 88]
    # A pointer reaches its string past any header, so none is stepped over.
    table = BlockConfig(PointerTableSource(0, 4, 2, 2), Pascal(1), "main", header=2)
    # The 05 is the first string's count, not a header byte.
    assert render(decode_strings(data, table, abc).tokens).startswith("[$CB][$02]AB")


def test_a_view_passes_over_the_padding_between_strings_as_the_block_does():
    """A string written shorter than its slot leaves the fill behind it, which
    the block passes over: a view that read it as text — an FF read as a length
    of 255 — would be out of step for the rest of its window."""
    abc = table_set(ABC_TABLE, "main")
    # "AB" written into a five-byte slot, then "B", then "BA".
    data = bytes.fromhex("02 41 42 FF FF  01 42  02 42 41")
    pascal = BlockConfig(RangeSource(0, len(data)), Pascal(1), "main")
    block = extract(data, pascal, abc)
    run = decode_strings(data, pascal, abc)
    assert [s.start for s in block.strings] == [0, 5, 7]
    assert run.starts == [s.start_bit for s in block.strings]
    assert render(run.tokens) == "AB[$FF][$FF]BBA"
    # A window may begin on the padding, which the block never does: it is
    # passed over there too, so the window's first string is the block's.
    run = decode_strings(data[4:], pascal, abc, 4)
    assert run.starts == [8, 24] and render(run.tokens) == "[$FF]BBA"


def test_a_view_passes_over_the_padding_in_front_of_a_record_header():
    """The padding comes before the next record's header, as it does in the
    block: the header is the string's, the padding the slot's."""
    abc = table_set(ABC_TABLE, "main")
    data = bytes.fromhex("05 CB 02 41 42  FF FF  06 CB 01 42  07 CB 02 42 41")
    pascal = BlockConfig(RangeSource(0, len(data)), Pascal(1), "main", header=2)
    block = extract(data, pascal, abc)
    run = decode_strings(data, pascal, abc)
    assert [s.start for s in block.strings] == [2, 9, 13]
    assert run.starts == [s.start_bit for s in block.strings]
    assert render(run.tokens) == "[$05][$CB]AB[$FF][$FF][$06][$CB]B[$07][$CB]BA"


def test_a_view_cuts_an_end_token_block_only_to_step_over_its_header():
    """End tokens say where a string ends, so a view reads them as the block
    does — but a record header still stands in front of each string, and only
    a cut steps over it. Without one the view keeps its resumable reading."""
    abc = table_set(ABC_TABLE, "main")
    # The header bytes are letters the table maps: read as text they would show
    # as characters and put every string start on the wrong byte.
    data = bytes.fromhex("41 42 43 00  41 43 42 00")
    cfg = BlockConfig(RangeSource(0, len(data)), EndToken(), "main", header=2)
    block = extract(data, cfg, abc)
    run = decode_strings(data, cfg, abc)
    assert run.starts == [s.start_bit for s in block.strings] == [16, 48]
    assert render(run.tokens) == "[$41][$42]C[end][$41][$43]B[end]"
    assert cuts_at_end_tokens(replace(cfg, header=0))
    assert not cuts_at_end_tokens(cfg)


def test_a_view_cuts_a_header_and_its_string_in_step_with_the_block():
    abc = table_set(ABC_TABLE, "main")
    data = bytes.fromhex("05 CB 41 42  06 CB 43 41")
    fixed = BlockConfig(RangeSource(0, len(data)), FixedLength(2), "main", header=2)
    run = decode_strings(data, fixed, abc)
    assert render(run.tokens) == "[$05][$CB]AB[$06][$CB]CA"
    assert run.starts == [16, 48]
    # The grid is the header and the length together: a view that starts
    # inside a string shows the rest of it, then whole records.
    run = decode_strings(data[3:], fixed, abc, 3)
    assert render(run.tokens) == "B[$06][$CB]CA"
    assert run.starts == [0, 24]
    # ...and one that starts inside a header shows the rest of the header.
    run = decode_strings(data[5:], fixed, abc, 5)
    assert render(run.tokens) == "[$CB]CA"
    assert run.starts == [8]


def test_pointer_cells_are_every_stride_from_the_table_s_start():
    data = bytes.fromhex("08 00 FF 0A 00 FF 40 00") + b"HI\x00OK\x00"
    source = PointerTableSource(0, 6, 2, 3)
    cells = pointer_cells(data, source, 1, 6)
    assert [(c.address, c.value, c.target) for c in cells] == [(3, 0x0A, 0x0A)]
    whole = pointer_cells(data, PointerTableSource(0, 9, 2, 3), 0, 9)
    # $40 points past the data, so it reaches nothing.
    assert [c.target for c in whole] == [8, 0x0A, None]
    listed = pointer_cells(data, PointerListSource((3, 0), 2), 0, 9)
    assert [c.address for c in listed] == [0, 3]


def test_a_pointer_s_string_reads_by_the_string_rules():
    data = b"HI\x00OK\x00"
    cfg = BlockConfig(PointerTableSource(0, 0, 2, 2), EndToken(), "ascii")
    assert render(target_string(Bits(data), cfg, _ascii(), 3)[0]) == "OK[end]"


def test_a_new_block_keeps_its_reading_s_kind_over_the_region():
    base = BlockConfig(RangeSource(0, 0), EndToken(), "t", skips=((1, 2),), bound=9)
    assert with_region(base, 4, 8).source == RangeSource(4, 8)
    assert with_region(base, 4, 8).skips == () and with_region(base, 4, 8).bound is None
    table = BlockConfig(PointerTableSource(0, 0, 3, 4, "big"), EndToken(), "t")
    assert with_region(table, 4, 8).source == PointerTableSource(4, 8, 3, 4, "big")
    listed = BlockConfig(PointerListSource((1, 2), 2), EndToken(), "t")
    assert with_region(listed, 4, 8).source == PointerTableSource(4, 8, 2, 2)


def test_a_pointer_s_string_is_read_for_a_bounded_preview():
    from mapchar.pipeline.view_read import PREVIEW_BYTES

    cfg = BlockConfig(PointerTableSource(0, 0, 2, 2), EndToken(), "ascii")
    data = b"A" * (PREVIEW_BYTES * 2)
    tokens, cut = target_string(Bits(data), cfg, _ascii(), 0)
    assert len(tokens) == PREVIEW_BYTES and cut
    ended = b"A" * (PREVIEW_BYTES - 1) + b"\x00"
    tokens, cut = target_string(Bits(ended), cfg, _ascii(), 0)
    assert len(tokens) == PREVIEW_BYTES and not cut


def test_a_view_s_pointer_window_holds_as_many_pointers_as_asked():
    from mapchar.pipeline.view_read import pointer_window

    table = PointerTableSource(4, 20, 2, 3)
    assert pointer_window(table, 0, 1) == 6  # the first pointer, at 4
    assert pointer_window(table, 5, 2) == 12  # the ones at 7 and 10
    assert pointer_window(table, 0, 6) == 21  # the last starts at 19
    assert pointer_window(table, 0, 7) is None  # only six start before 20
    listed = PointerListSource((30, 10, 20), 2)
    assert pointer_window(listed, 11, 2) == 32
    assert pointer_window(listed, 11, 3) is None


def _fixed(length: int = 5) -> BlockConfig:
    return BlockConfig(RangeSource(0, 0), FixedLength(length), "ascii")


def test_the_text_breaks_after_the_last_token_at_a_string_start():
    tokens = decode_strings(b"alpha", _fixed(), _ascii()).tokens
    # Nothing starts past these tokens: the text ends as the last string does.
    assert text_model(tokens, 0, 5, starts=[0]).body == "alpha"
    # The string in view begins where they end, so the text above them ends
    # with the break rather than running into the view's first line.
    assert text_model(tokens, 0, 5, starts=[0, 40]).body == "alpha\n"


def test_the_text_above_a_view_ends_where_the_view_s_string_begins():
    data = b"alphabravo"
    run = decode_strings(data, _fixed(), _ascii())
    assert run.starts == [0, 40]
    # A view at byte 5 starts a string: align_before keeps that start, so the
    # text above it is a string of its own.
    start, _tokens, starts = align_before(
        data,
        0,
        5,
        5,
        lambda d, at: decode_strings(d, _fixed(), _ascii(), at),
        tries=1,
        lookahead=5,
    )
    assert (start, starts) == (5, [0])
    start, tokens, starts = align_before(
        data,
        0,
        4,
        5,
        lambda d, at: decode_strings(d, _fixed(), _ascii(), at),
        tries=2,
        lookahead=5,
    )
    assert starts[-1] == (5 - start) * 8
    assert text_model(tokens, start, 5 - start, starts=starts).body.endswith("\n")


def test_the_kept_tokens_break_where_a_fresh_decode_does():
    data = b"alphabravo"
    tables = _ascii()
    cache = TextDecode(data, tables, 5)
    cache.extend(10, lambda d, t, at: decode_strings(d, _fixed(), t, at))
    above = decode_strings(data[:5], _fixed(), tables)
    # The string above joins the kept tokens at the byte they start at, which
    # is where a string begins: the junction breaks, as a fresh decode does.
    assert cache.prepend(0, above.tokens, [0, 40])
    fresh = text_model(above.tokens, 0, 5, starts=[0, 40])
    assert cache.above(0, 5).body == fresh.body == "alpha\n"
    assert cache.model(5, 10).body == "bravo"
    assert cache.chars[-1] == len("alpha\nbravo")


def test_a_view_reports_no_string_starting_past_its_data():
    # Nothing breaks after the last token of a whole decode: every start is a
    # string with tokens of its own, so the text gains no trailing break.
    data = b"alphabravo"
    tables = _ascii()
    run = decode_strings(data, _fixed(), tables)
    assert run.starts == [0, 40] and run.tokens[-1].bit_end == 80
    assert text_model(run.tokens, 0, 10, starts=run.starts).body == "alpha\nbravo"
    cache = TextDecode(data, tables, 0)
    cache.extend(10, lambda d, t, at: decode_strings(d, _fixed(), t, at))
    assert cache.model(0, 10).body == "alpha\nbravo"
