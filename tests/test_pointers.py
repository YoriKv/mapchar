from __future__ import annotations

from helpers import ABC_TABLE, pointer_rom, relayout, table_set, texts
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
from mapchar.plugins.base import Stage

TS = table_set(ABC_TABLE, "main")

ROM = pointer_rom((0x10, 0x13, 0x10), "41 42 00 43 00 41 41 41 00", tail=16)
"""Three pointers at 0, one a duplicate, to AB[end] C[end] AAA[end] at $10."""


def test_mappings_roundtrip(registry):
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
        m = resolve_mapping(registry, mid)
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
    assert resolve_mapping(registry, "lorom").to_offset(0x1234) is None
    assert resolve_mapping(registry, "gba").to_offset(0x1234) is None
    assert resolve_mapping(registry, "relative").to_offset(0x10, 0, 0x100) == 0x110
    assert resolve_mapping(registry, "bogus") is None
    assert resolve_mapping(registry, "banked:zz") is None
    assert "lorom" in registry.ids(Stage.MAPPING)


def test_pointer_table_extraction_merges_targets(registry):
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "linear"), EndToken(), "main"
    )
    ex = extract(ROM, cfg, TS, registry)
    assert texts(ex) == ["AB[end]", "C[end]"]
    assert [p.address for p in ex.strings[0].pointers] == [0, 4]
    assert ex.strings[0].pointers[0].value == 0x10


def test_pointer_list_and_next_pointer(registry):
    cfg = BlockConfig(
        PointerListSource((0, 2), 2, "little", "linear"), NextPointer(), "main"
    )
    ex = extract(ROM, cfg, TS, registry)
    assert texts(ex) == ["AB[end]", "C[end]"]
    assert (ex.strings[0].start, ex.strings[0].end) == (0x10, 0x13)
    cfg = BlockConfig(
        PointerListSource((0, 2), 2, "little", "linear"), FixedLength(2), "main"
    )
    ex = extract(ROM, cfg, TS, registry)
    assert texts(ex) == ["AB", "C[end]"]


def test_bad_pointers_are_notices(registry):
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "gba"), EndToken(), "main"
    )
    ex = extract(ROM, cfg, TS, registry)
    assert not ex.strings and len(ex.notices) == 3
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "nope"), EndToken(), "main"
    )
    assert not extract(ROM, cfg, TS, registry).strings


def test_packed_write_rewrites_pointers(registry):
    cfg = BlockConfig(
        PointerTableSource(0, 6, 2, 2, "little", "linear"),
        EndToken(),
        "main",
        bound=0x19,
        fill=0xEE,
    )
    assert cfg.effective_write_mode is WriteMode.PACKED
    res, out = relayout(ROM, cfg, TS, {0: "ABC[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x10:0x19] == bytes.fromhex("41 42 43 00 43 00 EE EE EE")
    assert out[0:6] == bytes.fromhex("10 00 14 00 10 00")
    assert texts(extract(out, cfg, TS, registry)) == ["ABC[end]", "C[end]"]


def test_discovery_finds_the_table(registry):
    starts = [0x10, 0x13]
    mappings = {mid: resolve_mapping(registry, mid) for mid in ("linear", "lorom")}
    cands = discover(ROM, starts, mappings, sizes=(2,), offsets=(0,))
    best = cands[0]
    assert (best.mapping_id, best.size, best.endian) == ("linear", 2, "little")
    assert best.explained == 2 and best.stride == 2
    src = best.source()
    assert src == PointerTableSource(0, 6, 2, 2, "little", "linear", 0)
    ex = extract(ROM, BlockConfig(src, EndToken(), "main"), TS, registry)
    assert len(ex.strings) == 2


def test_range_source_still_slotted():
    cfg = BlockConfig(RangeSource(0x10, 0x19), EndToken(), "main")
    assert cfg.effective_write_mode is WriteMode.SLOTTED
