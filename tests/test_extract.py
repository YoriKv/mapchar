from __future__ import annotations

from dataclasses import replace

from helpers import ABC_TABLE, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    WriteMode,
)
from mapchar.pipeline.extract import (
    extract,
    legacy_fixed_text,
    respell_fixed_end,
)
from mapchar.project.formats.blockspec import format_config, parse_config

TS = table_set(ABC_TABLE, "main")


def test_range_end_tokens():
    data = bytes.fromhex("41 42 00 43 00 41")
    ex = extract(data, BlockConfig(RangeSource(0, 6), EndToken(), "main"), TS)
    assert texts(ex) == ["AB[end]", "C[end]", "A"]
    assert [(s.start, s.end) for s in ex.strings] == [(0, 3), (3, 5), (5, 6)]


def test_range_stop_cuts_reading():
    data = bytes.fromhex("41 42 43 00")
    ex = extract(data, BlockConfig(RangeSource(0, 2), EndToken(), "main"), TS)
    assert texts(ex) == ["AB"]


def test_strings_per_pointer():
    data = bytes.fromhex("41 00 42 00 43 00")
    ex = extract(
        data,
        BlockConfig(RangeSource(0, 6), EndToken(), "main", strings_per_pointer=2),
        TS,
    )
    assert texts(ex) == ["A[end]B[end]", "C[end]"]


def test_lines():
    """Two line codes per string; an end token ends one early."""
    data = bytes.fromhex("41 FE 42 FE 43 FE 00 41 FE")
    ex = extract(data, BlockConfig(RangeSource(0, 9), Lines(2), "main"), TS)
    assert texts(ex) == ["A[line]\nB[line]\n", "C[line]\n[end]", "A[line]\n"]
    assert [(s.start, s.end) for s in ex.strings] == [(0, 4), (4, 7), (7, 9)]


def test_realign():
    data = bytes.fromhex("41 00 FF FF 42 00 FF FF")
    cfg = BlockConfig(RangeSource(0, 8), EndToken(), "main", realign=(4, 0))
    ex = extract(data, cfg, TS)
    assert texts(ex) == ["A[end]", "B[end]"]
    assert [(s.start, s.end) for s in ex.strings] == [(0, 4), (4, 8)]


def test_fixed_length_range():
    data = bytes.fromhex("41 42 00 43 41 41 42 43")
    cfg = BlockConfig(RangeSource(0, 8), FixedLength(4), "main")
    assert texts(extract(data, cfg, TS)) == ["AB[end]C", "AABC"]
    cfg = BlockConfig(
        RangeSource(0, 8), FixedLength(4, stop_at_end=True), "main", show_end=True
    )
    # The tail after the end token is not fill, so it shows, and writes back.
    assert texts(extract(data, cfg, TS)) == ["AB[end]C[end]\n", "AABC[end]\n"]


def test_fixed_source_with_lines():
    data = bytes.fromhex("41 42 43 41 42 43")
    cfg = BlockConfig(RangeSource(0, 6), FixedLength(3), "main", line_length=2)
    ex = extract(data, cfg, TS)
    assert texts(ex) == ["AB[line]\nC", "AB[line]\nC"]
    # A range past the data holds only the strings that fit.
    ex = extract(data, BlockConfig(RangeSource(0, 9), FixedLength(3), "main"), TS)
    assert len(ex.strings) == 2


def test_pascal_bytes_and_tokens():
    data = bytes.fromhex("02 41 42 01 43")
    cfg = BlockConfig(RangeSource(0, 5), Pascal(1), "main")
    ex = extract(data, cfg, TS)
    assert texts(ex) == ["AB", "C"]
    assert [(s.start, s.end) for s in ex.strings] == [(0, 3), (3, 5)]
    cfg = BlockConfig(RangeSource(0, 5), Pascal(1, counts_tokens=True), "main")
    assert texts(extract(data, cfg, TS)) == ["AB", "C"]


def test_skips():
    data = bytes.fromhex("41 FF FF 42 00")
    cfg = BlockConfig(RangeSource(0, 5), EndToken(), "main", skips=((1, 3),))
    assert texts(extract(data, cfg, TS)) == ["AB[end]"]


def test_pascal_strings_step_over_skips():
    """Records of a two-byte header, a length and the characters: skipping
    each header reads the records, each starting at its length."""
    data = bytes.fromhex("EE EE 02 41 42 EE EE 01 43")
    cfg = BlockConfig(RangeSource(0, 9), Pascal(1), "main", skips=((0, 2), (5, 7)))
    ex = extract(data, cfg, TS)
    assert texts(ex) == ["AB", "C"]
    assert [(s.start, s.end) for s in ex.strings] == [(2, 5), (7, 9)]
    # A skip inside the characters is stepped over too.
    data = bytes.fromhex("03 41 EE 42 43")
    cfg = BlockConfig(RangeSource(0, 5), Pascal(1), "main", skips=((2, 3),))
    assert texts(extract(data, cfg, TS)) == ["ABC"]


def test_backwards_skip_reads_every_string(registry):
    """A Cartographer ``AUTO JUMP`` whose stop is behind its start.

    Reading byte $9 continues at $4, so the two-byte ``X`` at $8 reads $8 and
    $4 and ends its run back at $5 -- behind where the run started. Both
    pointers still read both of their runs.
    """
    ts = table_set(ABC_TABLE + "4341=X\n", "main")
    # Pointers to $4 and $8, then A[end] B[end] at $4 and the 43 of X at $8.
    data = bytes.fromhex("04 00 08 00 41 00 42 00 43")
    cfg = BlockConfig(
        PointerListSource((0, 2), 2, "little", "linear"),
        EndToken(),
        "main",
        strings_per_pointer=2,
        skips=((9, 4),),
    )
    ex = extract(data, cfg, ts, registry)
    assert texts(ex) == ["A[end]B[end]", "X[end]B[end]"]
    assert [(s.start, s.end) for s in ex.strings] == [(4, 8), (8, 8)]


def test_padding_between_strings_is_passed_over():
    """The fill bytes a shorter replacement left in its slot are padding: the
    next string starts after them, whatever the block was written with."""
    data = bytes.fromhex("41 00 FF FF 42 00")
    cfg = BlockConfig(RangeSource(0, 6), EndToken(), "main")
    assert texts(extract(data, cfg, TS)) == ["A[end]", "B[end]"]


def test_a_mapped_fill_byte_is_read_as_text():
    """Padding is passed over only when the table maps nothing beginning with
    the fill byte. One it does map is text wherever it sits, so a string that
    begins with it keeps its head -- and every later string its index."""
    ts = table_set(ABC_TABLE + "FF=Z\n", "main")
    data = bytes.fromhex("41 42 00 FF 41 00 41 00")
    cfg = BlockConfig(RangeSource(0, 8), EndToken(), "main")
    assert texts(extract(data, cfg, ts)) == ["AB[end]", "ZA[end]", "A[end]"]


def test_a_fill_byte_that_is_the_end_token_keeps_its_empty_strings():
    """A block filled with its own end token: every one of them ends a string
    of its own, and none of them is passed over."""
    data = bytes.fromhex("41 00 00 42 00")
    cfg = BlockConfig(RangeSource(0, 5), EndToken(), "main", fill=b"\x00")
    assert texts(extract(data, cfg, TS)) == ["A[end]", "[end]", "B[end]"]


WORD_FILL = b"\xee\xdd"


def test_a_fixed_string_hides_its_end_token_and_the_fill_after_it():
    data = bytes.fromhex("41 42 00 EE DD EE  41 42 43 41 42 43  41 42 43 41 42 00")
    cfg = BlockConfig(RangeSource(0, 18), FixedLength(6, True), "main", fill=WORD_FILL)
    ex = extract(data, cfg, TS)
    assert texts(ex) == ["AB", "ABCABC", "ABCAB"]
    # The end token's bytes are still the string's.
    assert ex.strings[0].tokens[-1].is_end and ex.strings[0].end == 6
    # Show [end] still closes every fixed string with its own code.
    shown = BlockConfig(
        RangeSource(0, 18), FixedLength(6, True), "main", fill=WORD_FILL, show_end=True
    )
    assert texts(extract(data, shown, TS)) == [
        "AB[end]\n",
        "ABCABC[end]\n",
        "ABCAB[end]\n",
    ]


def test_a_fixed_string_whose_tail_is_not_fill_shows_it_after_its_end_token():
    data = bytes.fromhex("41 00 43 EE DD EE")
    cfg = BlockConfig(RangeSource(0, 6), FixedLength(6, True), "main", fill=WORD_FILL)
    assert texts(extract(data, cfg, TS)) == ["A[end]C[$EE][$DD][$EE]"]
    # A byte fill is repeated byte by byte, so this tail is not it either.
    single = BlockConfig(RangeSource(0, 6), FixedLength(6, True), "main", fill=b"\xee")
    data = bytes.fromhex("41 00 EE EE EE EE")
    assert texts(extract(data, single, TS)) == ["A"]


def test_a_run_of_fill_words_between_strings_is_padding():
    data = bytes.fromhex("41 00 EE DD EE DD 42 00")
    cfg = BlockConfig(RangeSource(0, 8), EndToken(), "main", fill=WORD_FILL)
    assert texts(extract(data, cfg, TS)) == ["A[end]", "B[end]"]
    cfg = BlockConfig(RangeSource(0, 8), EndToken(), "main", fill=b"\xee")
    assert texts(extract(data, cfg, TS)) == ["A[end]", "[$DD][$EE][$DD]B[end]"]


def test_a_text_saved_with_the_end_token_shown_is_respelled():
    """A project saved before fixed strings hid their end token holds originals
    that spell it: the one that is the bytes' old reading becomes today's, any
    other loses the end token that closes it."""
    data = bytes.fromhex("41 42 00 EE DD EE  41 00 43 EE DD EE")
    cfg = BlockConfig(RangeSource(0, 12), FixedLength(6, True), "main", fill=WORD_FILL)
    short, tail = extract(data, cfg, TS).strings
    assert legacy_fixed_text(short, cfg) == "AB[end]"
    assert legacy_fixed_text(tail, cfg) == "A[end]"
    assert respell_fixed_end("AB[end]", short, cfg, TS) == "AB"
    assert respell_fixed_end("CA[end]", short, cfg, TS) == "CA"
    assert respell_fixed_end("A[end]", tail, cfg, TS) == "A[end]C[$EE][$DD][$EE]"
    assert respell_fixed_end("ABCABC", short, cfg, TS) == "ABCABC"
    shown = BlockConfig(
        RangeSource(0, 12), FixedLength(6, True), "main", fill=WORD_FILL, show_end=True
    )
    short = extract(data, shown, TS).strings[0]
    assert legacy_fixed_text(short, shown) == "AB[end][end]\n"
    assert respell_fixed_end("AB[end][end]\n", short, shown, TS) == "AB[end]\n"
    assert respell_fixed_end("C[end][end]\n", short, shown, TS) == "C[end]\n"


def test_a_record_header_is_stepped_over_before_every_string():
    """``[2 bytes][length][text]`` records read with no skip range per record,
    and a write keeps each string in its slot with the headers left standing."""
    data = bytes.fromhex("05 CB 02 41 42  06 CB 01 42  07 CB 02 42 41")
    cfg = BlockConfig(RangeSource(0, len(data)), Pascal(1), "main", header=2)
    ts = TS
    ex = extract(data, cfg, ts)
    assert [(s.start, s.current_text()) for s in ex.strings] == [
        (2, "AB"),
        (7, "B"),
        (11, "BA"),
    ]
    assert cfg.effective_write_mode is WriteMode.SLOTTED
    res, out = relayout(data, cfg, ts, {0: "A"})
    assert res.ok, res.problems
    assert out == bytes.fromhex("05 CB 01 41 FF  06 CB 01 42  07 CB 02 42 41")
    # The padding is passed over, then the next record's header.
    again = extract(out, cfg, ts)
    assert [s.current_text() for s in again.strings] == ["A", "B", "BA"]
    # ...and is the string's to take back, up to that header and no further.
    res, back = relayout(out, cfg, ts, {0: "AB"})
    assert res.ok and back == data
    res, _ = relayout(out, cfg, ts, {0: "ABA"})
    assert not res.ok


def test_a_header_is_a_range_s_and_goes_in_the_config_line():
    cfg = BlockConfig(RangeSource(0, 8), Pascal(1), "main", header=2)
    assert "header=2" in format_config(cfg)
    assert parse_config(format_config(cfg)) == cfg
    table = PointerTableSource(0, 4, 2, 2, "little", "linear", 0)
    pointers = replace(cfg, source=table)
    assert pointers.record_header == 0
    assert pointers.effective_write_mode is WriteMode.PACKED
    # A block switched to pointers keeps the setting on screen and saves none
    # of it: a pointer reaches its string past any header.
    assert "header=" not in format_config(pointers)


def test_a_header_settles_the_write_mode_as_a_skip_range_does():
    """Packed would lay the strings over the headers, so a header forces
    slotted whatever the block's mode says."""
    data = bytes.fromhex("05 CB 02 41 42  06 CB 01 42  07 CB 02 42 41")
    cfg = BlockConfig(
        RangeSource(0, len(data)),
        Pascal(1),
        "main",
        header=2,
        write_mode=WriteMode.PACKED,
    )
    assert cfg.effective_write_mode is WriteMode.SLOTTED
    res, out = relayout(data, cfg, TS, {0: "A"})
    assert res.ok, res.problems
    assert out == bytes.fromhex("05 CB 01 41 FF  06 CB 01 42  07 CB 02 42 41")
    skipped = BlockConfig(
        RangeSource(0, 5),
        EndToken(),
        "main",
        skips=((1, 3),),
        write_mode=WriteMode.PACKED,
    )
    assert skipped.effective_write_mode is WriteMode.SLOTTED


def test_a_header_before_every_kind_of_string():
    """The header is the record's, whatever ends the string in it; a header
    with no room for a string after it ends the reading."""
    fixed = bytes.fromhex("05 CB 41 42  06 CB 43 41  07 CB")
    cfg = BlockConfig(RangeSource(0, len(fixed)), FixedLength(2), "main", header=2)
    assert [(s.start, s.current_text()) for s in extract(fixed, cfg, TS).strings] == [
        (2, "AB"),
        (6, "CA"),
    ]
    ended = bytes.fromhex("05 CB 41 42 00  06 CB 43 00")
    cfg = replace(cfg, source=RangeSource(0, len(ended)), string_type=EndToken())
    assert [(s.start, s.current_text()) for s in extract(ended, cfg, TS).strings] == [
        (2, "AB[end]"),
        (7, "C[end]"),
    ]
    lined = bytes.fromhex("05 CB 41 FE  06 CB 42 FE")
    cfg = replace(cfg, source=RangeSource(0, len(lined)), string_type=Lines(1))
    assert [s.start for s in extract(lined, cfg, TS).strings] == [2, 6]


def test_a_header_steps_over_the_skip_ranges_in_front_of_its_string():
    """A header is read where the block's skips leave it, as the string it
    belongs to is."""
    data = bytes.fromhex("05 CB 41 00  06 EE EE CB 42 00")
    cfg = BlockConfig(
        RangeSource(0, len(data)),
        EndToken(),
        "main",
        header=2,
        skips=((5, 7),),
    )
    assert [(s.start, s.current_text()) for s in extract(data, cfg, TS).strings] == [
        (2, "A[end]"),
        (8, "B[end]"),
    ]


def test_a_record_that_leaves_the_position_where_it_was_ends_the_reading():
    """No control sets a header behind the position, but a hand-edited project
    could, and a record that does not advance would be read for ever."""
    data = bytes.fromhex("41 42 43 00  41 42 00 43")
    cfg = BlockConfig(RangeSource(4, 8), EndToken(), "main", header=-1)
    assert texts(extract(data, cfg, TS)) == []
