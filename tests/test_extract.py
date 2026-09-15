from __future__ import annotations

from helpers import ABC_TABLE, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Pascal,
    PointerListSource,
    RangeSource,
)
from mapchar.pipeline.extract import extract

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
    assert texts(extract(data, cfg, TS)) == ["AB[end][end]\n", "AABC[end]\n"]


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
