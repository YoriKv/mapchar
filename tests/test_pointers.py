from __future__ import annotations

from helpers import table_set
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    NextPointer,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    WriteMode,
)
from mapchar.core.mapping import resolve_mapping
from mapchar.engines.pointers import discover
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, layout_block
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import default_registry

TS = table_set("@table main\n41=A\n42=B\n43=C\n/00=[end]\n", "main")
REG = default_registry()


def test_mappings_roundtrip():
    cases = {
        "linear": [(0, 0), (0x1234, 0x1234)],
        "lorom": [
            (0, 0x008000),
            (0x7FFF, 0x00FFFF),
            (0x8000, 0x018000),
            (0x12345, 0x02A345),
        ],
        "hirom": [(0, 0xC00000), (0x12345, 0xC12345)],
        "gb": [(0x100, 0x100), (0x4000, 0x14000), (0x8123, 0x24123)],
        "gba": [(0x10, 0x08000010)],
        "banked": [(0x100, 0x8100), (0x4100, 0x8100)],
        "banked:C000:4000": [(0x100, 0xC100)],
    }
    for mid, pairs in cases.items():
        m = resolve_mapping(REG, mid)
        for offset, value in pairs:
            assert m.to_value(offset) == value, mid
            bank = (
                offset // getattr(m, "bank_size", 0x8000)
                if getattr(m, "needs_bank", False)
                else 0
            )
            if mid == "gb":
                bank = offset // 0x4000
            back = m.to_offset(value, bank)
            assert back == offset, (mid, value)
    assert resolve_mapping(REG, "lorom").to_offset(0x1234) is None
    assert resolve_mapping(REG, "gba").to_offset(0x1234) is None
    assert resolve_mapping(REG, "relative").to_offset(0x10, 0, 0x100) == 0x110
    assert resolve_mapping(REG, "bogus") is None
    assert resolve_mapping(REG, "banked:zz") is None
    assert "lorom" in REG.ids(Stage.MAPPING)


def pointer_rom() -> bytes:
    # Table at 0: three little-endian 16-bit linear pointers (one duplicate).
    table = (
        (0x10).to_bytes(2, "little")
        + (0x13).to_bytes(2, "little")
        + (0x10).to_bytes(2, "little")
    )
    body = bytes.fromhex(
        "41 42 00 43 00 41 41 41 00"
    )  # at $10: AB[end] C[end] AAA[end]
    return table + b"\xff" * (0x10 - len(table)) + body + b"\xff" * 16


def test_pointer_table_extraction_merges_targets():
    data = pointer_rom()
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "linear"), EndToken(), "main"
    )
    ex = extract(data, cfg, TS, REG)
    assert [s.original_text() for s in ex.strings] == ["AB[end]", "C[end]"]
    assert [p.address for p in ex.strings[0].pointers] == [0, 4]
    assert ex.strings[0].pointers[0].value == 0x10


def test_pointer_list_and_next_pointer():
    data = pointer_rom()
    cfg = BlockConfig(
        PointerListSource((0, 2), 2, "little", "linear"), NextPointer(), "main"
    )
    ex = extract(data, cfg, TS, REG)
    assert [s.original_text() for s in ex.strings] == ["AB[end]", "C[end]"]
    assert (ex.strings[0].start, ex.strings[0].end) == (0x10, 0x13)
    cfg = BlockConfig(
        PointerListSource((0, 2), 2, "little", "linear"), FixedLength(2), "main"
    )
    ex = extract(data, cfg, TS, REG)
    assert [s.original_text() for s in ex.strings] == ["AB", "C[end]"]


def test_bad_pointers_are_notices():
    data = pointer_rom()
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "gba"), EndToken(), "main"
    )
    ex = extract(data, cfg, TS, REG)
    assert not ex.strings and len(ex.notices) == 3
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "nope"), EndToken(), "main"
    )
    assert not extract(data, cfg, TS, REG).strings


def test_packed_write_rewrites_pointers():
    data = pointer_rom()
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "linear"),
        EndToken(),
        "main",
        bound=0x19,
        fill=0xEE,
    )
    assert cfg.effective_write_mode is WriteMode.PACKED
    ex = extract(data, cfg, TS, REG)
    ex.strings[0].translation = "ABC[end]"
    res = layout_block(data, cfg, TS, ex.strings, REG)
    assert res.ok, res.problems
    out = apply_splices(data, res.splices)
    assert out[0x10:0x19] == bytes.fromhex("41 42 43 00 43 00 EE EE EE")
    assert out[0:6] == bytes.fromhex("10 00 14 00 10 00")
    ex2 = extract(out, cfg, TS, REG)
    assert [s.original_text() for s in ex2.strings] == ["ABC[end]", "C[end]"]


def test_discovery_finds_the_table():
    data = pointer_rom()
    starts = [0x10, 0x13]
    mappings = {mid: resolve_mapping(REG, mid) for mid in ("linear", "lorom")}
    cands = discover(data, starts, mappings, sizes=(2,), offsets=(0,))
    best = cands[0]
    assert (best.mapping_id, best.size, best.endian) == ("linear", 2, "little")
    assert best.explained == 2 and best.stride == 2
    src = best.source()
    assert src == PointerTableSource(0, 6, 2, 2, "little", "linear", 0)
    ex = extract(data, BlockConfig(src, EndToken(), "main"), TS, REG)
    assert len(ex.strings) == 2


def test_range_source_still_slotted():
    cfg = BlockConfig(RangeSource(0x10, 0x19), EndToken(), "main")
    assert cfg.effective_write_mode is WriteMode.SLOTTED
