from __future__ import annotations

from helpers import table_set
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    FixedSource,
    Pascal,
    RangeSource,
    WriteMode,
)
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, layout_block

TS = table_set("@table main\n41=A\n42=B\n43=C\n/00=[end]\nFE=[line]\\n\n", "main")


def run(data, cfg, edits):
    ex = extract(data, cfg, TS)
    for i, text in edits.items():
        ex.strings[i].translation = text
    res = layout_block(data, cfg, TS, ex.strings)
    return res, (apply_splices(data, res.splices) if res.ok else None)


def test_slotted_default_without_pointers():
    data = bytes.fromhex("41 42 00 43 00 41 41")
    cfg = BlockConfig(RangeSource(0, 7), EndToken(), "main", fill=0xEE)
    assert cfg.effective_write_mode is WriteMode.SLOTTED
    res, out = run(data, cfg, {0: "A[end]"})
    assert res.ok and out == bytes.fromhex("41 00 EE 43 00 41 41")
    res, out = run(data, cfg, {1: "BB[end]"})
    assert not res.ok and res.problems[0].index == 1 and res.problems[0].over == 1
    res, out = run(data, cfg, {0: "AB"})
    assert not res.ok and "end token" in res.problems[0].message


def test_packed_mode_with_bound():
    data = bytes.fromhex("41 42 00 43 00 41 41")
    cfg = BlockConfig(
        RangeSource(0, 7), EndToken(), "main", write_mode=WriteMode.PACKED, fill=0xEE
    )
    res, out = run(data, cfg, {0: "A[end]", 1: "CC[end]"})
    # String 2 is untouched, so its original bytes (no end token) are reused.
    assert res.ok and out == bytes.fromhex("41 00 43 43 00 41 41")
    assert res.used == 7 and res.available == 7
    res, out = run(data, cfg, {2: "AAAAA[end]"})
    assert not res.ok and res.problems[0].over == 4


def test_packed_realign():
    data = bytes.fromhex("41 00 FF FF 42 00 FF FF")
    cfg = BlockConfig(
        RangeSource(0, 8),
        EndToken(),
        "main",
        realign=(4, 0),
        write_mode=WriteMode.PACKED,
        fill=0xEE,
    )
    res, out = run(data, cfg, {0: "AA[end]"})
    # The untouched second string keeps its realignment padding bytes.
    assert res.ok and out == bytes.fromhex("41 41 00 EE 42 00 FF FF")


def test_fixed_strings_and_lines():
    data = bytes.fromhex("41 42 43 41 42 43")
    cfg = BlockConfig(
        FixedSource(0, 2, 3), FixedLength(3), "main", line_length=2, fill=0xEE
    )
    res, out = run(data, cfg, {0: "B[line]\nC"})
    assert res.ok and out == bytes.fromhex("42 EE 43 41 42 43")
    res, out = run(data, cfg, {0: "BBB[line]C"})
    assert not res.ok and "line 1" in res.problems[0].message
    cfg = BlockConfig(
        RangeSource(0, 6), FixedLength(3, True), "main", show_end=True, fill=0xEE
    )
    res, out = run(data, cfg, {1: "A[end][end]\n"})
    assert res.ok and out == bytes.fromhex("41 42 43 41 00 EE")


def test_pascal():
    data = bytes.fromhex("02 41 42 01 43")
    cfg = BlockConfig(
        RangeSource(0, 5),
        Pascal(1),
        "main",
        write_mode=WriteMode.PACKED,
        bound=5,
        fill=0xEE,
    )
    res, out = run(data, cfg, {0: "A"})
    assert res.ok and out == bytes.fromhex("01 41 01 43 EE")


def test_untouched_strings_write_original_bytes():
    data = bytes.fromhex("41 99 00 42 00")
    cfg = BlockConfig(RangeSource(0, 5), EndToken(), "main")
    res, out = run(data, cfg, {})
    assert res.ok and out == data
