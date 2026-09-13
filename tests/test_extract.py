from __future__ import annotations

from helpers import table_set
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    FixedSource,
    Pascal,
    RangeSource,
)
from mapchar.pipeline.extract import extract

TS = table_set("@table main\n41=A\n42=B\n43=C\n/00=[end]\nFE=[line]\\n\n", "main")


def texts(ex):
    return [s.original_text() for s in ex.strings]


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
    assert texts(extract(data, cfg, TS)) == ["AB[end][end]", "AABC[end]"]


def test_fixed_source_with_lines():
    data = bytes.fromhex("41 42 43 41 42 43")
    cfg = BlockConfig(FixedSource(0, 2, 3), FixedLength(3), "main", line_length=2)
    ex = extract(data, cfg, TS)
    assert texts(ex) == ["AB[line]C", "AB[line]C"]
    ex = extract(data, BlockConfig(FixedSource(0, 3, 3), FixedLength(3), "main"), TS)
    assert len(ex.strings) == 2 and ex.notices


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
