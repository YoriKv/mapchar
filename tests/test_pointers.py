from __future__ import annotations

from helpers import ABC_TABLE, pointer_rom, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    NestedPointerSource,
    NextPointer,
    PointerListSource,
    PointerRef,
    PointerTableSource,
    RangeSource,
    WriteMode,
)
from mapchar.engines.pointers import discover
from mapchar.pipeline.extract import extract, reextract
from mapchar.pipeline.insert import apply_splices, layout_block, string_ends
from mapchar.pipeline.view_read import pointer_cells, pointer_window
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import resolve_mapping
from mapchar.project.exchange.addresses import shift_config

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
        fill=b"\xee",
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


def test_discovery_offset_range_widens_the_candidate_space(registry):
    """An offset is the part of a pointer's arithmetic a ROM chooses freely, so a
    table based somewhere other than the string it names is found only by trying
    the offsets it might be based on."""
    rom = pointer_rom((0x0E, 0x11), "41 42 00 43 00")
    starts = [0x10, 0x13]
    mappings = {"linear": resolve_mapping(registry, "linear")}
    assert not discover(rom, starts, mappings, sizes=(2,), offsets=(0,))
    cands = discover(rom, starts, mappings, sizes=(2,), offsets=(0, 2))
    assert all(c.offset == 2 for c in cands)
    best = cands[0]
    assert (best.explained, best.endian) == (2, "little")
    assert best.source() == PointerTableSource(0, 4, 2, 2, "little", "linear", 2)


def test_discovery_refs_carry_the_whole_reading(registry):
    """What **Attach** puts on the strings: where the value sits plus everything
    needed to write it back, with no second lookup."""
    mappings = {"linear": resolve_mapping(registry, "linear")}
    best = discover(ROM, [0x10, 0x13], mappings, sizes=(2,), offsets=(0,))[0]
    refs = best.refs()
    assert [p.address for p in refs[0x10]] == [0, 4]  # one target, two pointers
    assert refs[0x13] == (PointerRef(2, 2, "little", "linear", 0, 0x13),)
    assert {p.value for p in refs[0x10]} == {0x10}


NESTED_ROM = bytes.fromhex(
    "10 00 14 00  00 00 00 00  20 00 24 00  EE EE EE EE"  # outer: 3 records
    "01 00 04 00  FF 41 42 00  43 00 FF FF  FF FF FF FF"  # map 0: table, text
    "00 00 01 00  FF 41 00 FF  FF FF FF FF  EE EE EE EE"  # map 1: table, text
)
"""An outer table of ``(inner table, base)`` u16 pairs: map 0's offsets at $10
count from $14 and reach AB[end] and C[end], the second record is null, and map
1's first offset is null and its second reaches A[end] at $25."""

NESTED = NestedPointerSource(0, 0xC, 2, 4, null=0, inner_size=2, inner_null=0)


def test_a_nested_source_reads_every_inner_table_from_its_own_base(registry):
    cfg = BlockConfig(NESTED, EndToken(), "main")
    ex = extract(NESTED_ROM, cfg, TS, registry)
    assert not ex.notices
    assert texts(ex) == ["AB[end]", "C[end]", "A[end]"]
    assert [s.pointers for s in ex.strings] == [
        (PointerRef(0x10, 2, "little", "linear", 0x14, 1),),
        (PointerRef(0x12, 2, "little", "linear", 0x14, 4),),
        (PointerRef(0x22, 2, "little", "linear", 0x24, 1),),
    ]
    # Without the null values, the empty record and offset read as pointers.
    plain = BlockConfig(NestedPointerSource(0, 0xC, 2, 4), EndToken(), "main")
    ex = extract(NESTED_ROM, plain, TS, registry)
    assert [s.start for s in ex.strings] == [0x15, 0x18, 0x24, 0x25]


def test_a_nested_block_lays_out_each_group_over_its_own_text(registry):
    cfg = BlockConfig(NESTED, EndToken(), "main", fill=b"\xff")
    assert cfg.effective_write_mode is WriteMode.PACKED
    res, out = relayout(NESTED_ROM, cfg, TS, {0: "ABCABC[end]"}, registry)
    assert res.ok, res.problems
    # Map 0's text grows into its padding, its second offset moves, map 1 and
    # the outer table stay as they are.
    assert out[0x10:0x20] == bytes.fromhex(
        "01 00 08 00 FF 41 42 43 41 42 43 00 43 00 FF FF"
    )
    assert out[:0x10] == NESTED_ROM[:0x10] and out[0x20:] == NESTED_ROM[0x20:]
    assert texts(extract(out, cfg, TS, registry)) == ["ABCABC[end]", "C[end]", "A[end]"]
    # Nothing crosses into the next map's table.
    res, _ = relayout(NESTED_ROM, cfg, TS, {1: "CCCCCCCCC[end]"}, registry)
    assert not res.ok and "bound" in res.problems[0].message
    # Room given up is room to take back.
    res, out = relayout(NESTED_ROM, cfg, TS, {0: "[end]", 1: "[end]"}, registry)
    assert res.ok and out[0x14:0x20] == bytes.fromhex(
        "FF 00 00 FF FF FF FF FF FF FF FF FF"
    )
    res, out = relayout(out, cfg, TS, {0: "ABCAB[end]", 1: "CCCC[end]"}, registry)
    assert res.ok, res.problems


def test_a_nested_edit_lays_out_only_its_own_group(registry):
    cfg = BlockConfig(NESTED, EndToken(), "main")
    ex = extract(NESTED_ROM, cfg, TS, registry)
    ex.strings[2].replacement = "B[end]"
    res = layout_block(NESTED_ROM, cfg, TS, ex.strings, registry)
    assert res.ok and set(res.encoded) == {2}
    lo = min(s.offset for s in res.splices)
    hi = max(s.end for s in res.splices)
    assert 0x20 <= lo and hi <= 0x30
    out = apply_splices(NESTED_ROM, res.splices)
    # Reading only what changed gives what reading everything does.
    again = reextract(out, cfg, TS, ex.strings, lo, hi, registry)
    assert again is not None
    assert [(s.start, s.end, s.current_text(), s.pointers) for s in again.strings] == [
        (s.start, s.end, s.current_text(), s.pointers)
        for s in extract(out, cfg, TS, registry).strings
    ]
    assert again.strings[0] is ex.strings[0]
    # The outer table is the whole block's.
    assert reextract(out, cfg, TS, ex.strings, 0, 4, registry) is None


def test_a_nested_block_in_slotted_mode_keeps_every_string_in_place(registry):
    cfg = BlockConfig(NESTED, EndToken(), "main", write_mode=WriteMode.SLOTTED)
    ex = extract(NESTED_ROM, cfg, TS, registry)
    ends = string_ends(NESTED_ROM, cfg, ex.strings, registry)
    assert ends == {0: 0x18, 1: 0x20, 2: 0x2C}
    res, out = relayout(NESTED_ROM, cfg, TS, {1: "CCCCC[end]"}, registry)
    assert res.ok and out[0x18:0x20] == bytes.fromhex("43 43 43 43 43 00 FF FF")
    assert out[:0x18] == NESTED_ROM[:0x18]
    packed = BlockConfig(NESTED, EndToken(), "main")
    assert string_ends(NESTED_ROM, packed, ex.strings, registry) == {
        0: 0x20,
        1: 0x20,
        2: 0x2C,
    }


def test_a_nested_source_s_pointers_in_view(registry):
    cells = pointer_cells(NESTED_ROM, NESTED, 0, 0x30, registry)
    assert [(c.address, c.target, c.null) for c in cells] == [
        (0x0, 0x10, False),
        (0x2, 0x14, False),
        (0x4, None, True),
        (0x6, None, True),
        (0x8, 0x20, False),
        (0xA, 0x24, False),
        (0x10, 0x15, False),
        (0x12, 0x18, False),
        (0x20, None, True),
        (0x22, 0x25, False),
    ]
    assert pointer_window(NESTED, 0x6, 3, NESTED_ROM, registry) == 0xC
    assert pointer_window(NESTED, 0xC, 2, NESTED_ROM, registry) == 0x14


def test_a_nested_source_moves_with_the_header():
    cfg = shift_config(BlockConfig(NESTED, EndToken(), "main"), 0x200)
    assert (cfg.source.start, cfg.source.stop, cfg.source.offset) == (
        0x200,
        0x20C,
        0x200,
    )


def test_a_null_pointer_reaches_no_string(registry):
    rom = pointer_rom((0x10, 0x00, 0x13), "41 42 00 43 00")
    table = BlockConfig(PointerTableSource(0, 6, 2, 2, null=0), EndToken(), "main")
    ex = extract(rom, table, TS, registry)
    assert texts(ex) == ["AB[end]", "C[end]"]
    assert [p.address for s in ex.strings for p in s.pointers] == [0, 4]
    res, out = relayout(rom, table, TS, {0: "A[end]"}, registry)
    assert res.ok and out[:6] == bytes.fromhex("10 00 00 00 12 00")
    listed = BlockConfig(PointerListSource((0, 2, 4), 2, null=0), EndToken(), "main")
    assert texts(extract(rom, listed, TS, registry)) == ["AB[end]", "C[end]"]
    cells = pointer_cells(rom, table.source, 0, 6, registry)
    assert [(c.target, c.null) for c in cells] == [
        (0x10, False),
        (None, True),
        (0x13, False),
    ]


def test_a_string_that_is_another_s_tail_is_laid_out_once(registry):
    rom = pointer_rom((0x10, 0x12, 0x14), "41 42 43 00 41 00")
    cfg = BlockConfig(PointerTableSource(0, 6, 2, 2), EndToken(), "main", bound=0x18)
    assert texts(extract(rom, cfg, TS, registry)) == ["ABC[end]", "C[end]", "A[end]"]
    res, out = relayout(rom, cfg, TS, {2: "AA[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x10:0x17] == bytes.fromhex("41 42 43 00 41 41 00")
    assert out[:6] == bytes.fromhex("10 00 12 00 14 00")
    # Still the tail when the string it ends changes and ends the same way.
    res, out = relayout(rom, cfg, TS, {0: "BC[end]"}, registry)
    assert res.ok and out[:6] == bytes.fromhex("10 00 11 00 13 00")
    # Edited apart, each has bytes of its own.
    res, out = relayout(rom, cfg, TS, {0: "A[end]", 1: "B[end]"}, registry)
    assert res.ok and out[0x10:0x16] == bytes.fromhex("41 00 42 00 41 00")
    # Laid out apart, they stay apart.
    res, out = relayout(out, cfg, TS, {0: "ABC[end]", 1: "C[end]"}, registry)
    assert res.ok and out[:6] == bytes.fromhex("10 00 14 00 16 00")
    # A range has no pointers to read a tail through.
    ranged = BlockConfig(
        RangeSource(0x10, 0x16), EndToken(), "main", write_mode=WriteMode.PACKED
    )
    res, out = relayout(rom, ranged, TS, {1: "C[end]"}, registry)
    assert res.ok and out[0x10:0x16] == bytes.fromhex("41 42 43 00 43 00")
