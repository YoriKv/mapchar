from __future__ import annotations

import time

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


def test_discovery_leaves_stray_matches_out_of_the_table(registry):
    """A pointer value that also turns up far from the table is a hit, but the
    table inferred is the run the strides join — and **Attach** leaves the stray
    off a string the run already reaches, since a packed write would rewrite it
    wherever it sits."""
    rom = pointer_rom((0x10, 0x13), "41 42 00 43 00") + bytes(40) + b"\x13\x00"
    mappings = {"linear": resolve_mapping(registry, "linear")}
    best = discover(rom, [0x10, 0x13], mappings, sizes=(2,), offsets=(0,))[0]
    assert len(best.addresses) == 3
    assert best.table_run() == [0, 2]
    assert best.source() == PointerTableSource(0, 4, 2, 2, "little", "linear", 0)
    assert [p.address for p in best.refs()[0x13]] == [2]


def test_discovery_takes_each_banked_pointer_s_bank_from_its_own_string(registry):
    """A Game Boy pointer holds only the address inside the switched window, so
    which bank it is read in comes from where its string sits — the search is
    never told a bank, and the strings of one block need not share one. The
    table carries the bank its run mostly reads in.
    """
    rom = bytearray(b"\xff" * 0x8020)
    rom[0x0100:0x0102] = bytes.fromhex("41 00")  # A[end], in the fixed bank
    rom[0x8000:0x8006] = bytes.fromhex("10 40 13 40 00 01")  # $4010 $4013 $0100
    rom[0x8010:0x8015] = bytes.fromhex("41 42 00 43 00")  # AB[end] C[end]
    starts = [0x8010, 0x8013, 0x0100]
    mappings = {"gb": resolve_mapping(registry, "gb")}
    best = discover(bytes(rom), starts, mappings, sizes=(2,), offsets=(0,))[0]
    assert best.banks == {0x8010: 2, 0x8013: 2, 0x0100: 0}
    src = best.source()
    assert src == PointerTableSource(0x8000, 0x8006, 2, 2, "little", "gb", 0, 2)
    ex = extract(bytes(rom), BlockConfig(src, EndToken(), "main"), TS, registry)
    assert texts(ex) == ["A[end]", "AB[end]", "C[end]"]


def test_discovery_ranks_a_table_above_a_candidate_of_strays(registry):
    """A short pointer value turns up all over a file, so the strings a
    candidate explains counts coincidences as readily as pointers: what ranks
    first is the strings its table run explains."""
    rom = bytearray(b"\xff" * 0x40)
    rom[0:6] = bytes.fromhex("10 00 13 00 18 00")  # the table, little-endian
    rom[0x10:0x1E] = bytes.fromhex("41 42 00 43 43 43 43 00 41 00 FF FF 42 00")
    # The same values read big-endian: two inside the table and two far from it.
    rom[0x30:0x32] = bytes.fromhex("00 10")
    rom[0x3D:0x3F] = bytes.fromhex("00 1C")
    mappings = {"linear": resolve_mapping(registry, "linear")}
    cands = discover(bytes(rom), [0x10, 0x13, 0x18, 0x1C], mappings, sizes=(2,))
    best, other = cands[0], cands[1]
    assert (best.endian, best.run_explained(), best.explained) == ("little", 3, 3)
    # Explains one string more, and only by chance: its run reaches two.
    assert (other.endian, other.run_explained(), other.explained) == ("big", 2, 4)
    assert best.source() == PointerTableSource(0, 6, 2, 2, "little", "linear", 0)


def test_discovery_keeps_a_field_of_fill_out_of_the_stride_and_the_run(registry):
    """One string whose value is a common byte pair hits every other address of
    a stretch of fill. Neither the stride nor the run is its to decide: the
    stride is voted on by the strings that are not everywhere, and the run that
    wins is the one reaching the most strings, not the one holding the most
    addresses."""
    rom = bytearray(b"\xff" * 0x107 + bytes(0x14000))
    rom[0:6] = bytes.fromhex("00 00 03 00 05 00")  # the table, based at $100
    rom[0x100:0x107] = bytes.fromhex("41 42 00 43 00 41 00")
    mappings = {"linear": resolve_mapping(registry, "linear")}
    starts = [0x100, 0x103, 0x105]
    best = discover(bytes(rom), starts, mappings, sizes=(2,), offsets=(0x100,))[0]
    assert len(best.hits[0x100]) > 0x10000  # the fill, plus the table's own
    assert best.stride == 2
    assert best.table_run() == [0, 2, 4]
    assert best.run_explained() == 3
    assert best.source() == PointerTableSource(0, 6, 2, 2, "little", "linear", 0x100, 0)


def test_discovery_over_a_file_of_fill_is_quick(registry):
    """A million hits is a million addresses to sort, join into runs and hand
    to the dialog three times over: worked out once per candidate, not once per
    question asked of it."""
    rom = bytearray(b"\xff" * 0x107 + bytes((1 << 20) - 0x107))
    rom[0:6] = bytes.fromhex("00 00 03 00 05 00")
    rom[0x100:0x107] = bytes.fromhex("41 42 00 43 00 41 00")
    mappings = {"linear": resolve_mapping(registry, "linear")}
    starts = [0x100, 0x103, 0x105]
    began = time.perf_counter()
    cands = discover(bytes(rom), starts, mappings, sizes=(2,), offsets=(0x100,))
    best = cands[0]
    rows = [(c.bank(), c.table_run(), c.explained) for c in cands]
    assert time.perf_counter() - began < 5
    assert rows[0][1] == [0, 2, 4]
    assert best.source() == PointerTableSource(0, 6, 2, 2, "little", "linear", 0x100, 0)


BANK_ORDER = [0, 2, 1, 3, 6, 7, 4, 5]
"""The bank table the example mapping is built around: bank numbers in the
mapper's own order rather than the file's."""


class TableBanked:
    """The shipped example mapping: banked through a bank table, and with no
    ``bank_of`` to say which bank an offset is reached in."""

    sizes = (2, 3)
    needs_bank = True

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        address = value & 0xFFFF
        if value > 0xFFFF:
            bank = value >> 16
        if not 0x8000 <= address < 0xC000 or bank >= len(BANK_ORDER):
            return None
        return BANK_ORDER[bank] * 0x4000 + (address - 0x8000)

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return 0x8000 + (offset % 0x4000)


class BrokenMapping:
    """A mapping that raises, which a plugin is always free to do."""

    sizes = (2,)
    needs_bank = True

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        raise RuntimeError("no")

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        raise RuntimeError("no")


def test_discovery_finds_a_banked_table_a_mapping_cannot_place(registry):
    """A mapping that needs a bank but cannot say which one an offset sits in
    leaves the search with the bank it was given, which is a guess — so the
    guess does not get to veto a pointer that was found."""
    rom = bytearray(b"\xff" * 0x8020)
    rom[0x100:0x104] = bytes.fromhex("10 80 13 80")
    rom[0x8010:0x8015] = bytes.fromhex("41 42 00 43 00")
    mappings = {"my_table_banked": TableBanked()}
    best = discover(bytes(rom), [0x8010, 0x8013], mappings, sizes=(2,), offsets=(0,))[0]
    assert best.explained == 2
    assert best.table_run() == [0x100, 0x102]
    assert best.banks == {0x8010: 0, 0x8013: 0}


def test_a_mapping_that_raises_loses_only_its_own_combination(registry):
    mappings = {
        "broken": BrokenMapping(),
        "linear": resolve_mapping(registry, "linear"),
    }
    cands = discover(ROM, [0x10, 0x13], mappings, sizes=(2,), offsets=(0,))
    assert cands and all(c.mapping_id == "linear" for c in cands)


def test_discovery_takes_the_table_s_bank_from_the_pointers_that_need_one(registry):
    """A pointer into the Game Boy's fixed bank reads the same in every bank,
    so it says nothing about which one the table is read in: the bank is voted
    on by the pointers whose target moves with it."""
    rom = bytearray(b"\xff" * 0x8020)
    rom[0:6] = bytes.fromhex("00 01 50 01 10 40")  # $0100 $0150 $4010
    rom[0x100:0x102] = bytes.fromhex("41 00")
    rom[0x150:0x152] = bytes.fromhex("42 00")
    rom[0x8010:0x8012] = bytes.fromhex("43 00")
    mappings = {"gb": resolve_mapping(registry, "gb")}
    starts = [0x100, 0x150, 0x8010]
    best = discover(bytes(rom), starts, mappings, sizes=(2,), offsets=(0,))[0]
    assert best.banks == {0x100: 0, 0x150: 0, 0x8010: 2}
    assert best.bank() == 2
    src = best.source()
    assert src == PointerTableSource(0, 6, 2, 2, "little", "gb", 0, 2)
    assert texts(
        extract(bytes(rom), BlockConfig(src, EndToken(), "main"), TS, registry)
    ) == [
        "A[end]",
        "B[end]",
        "C[end]",
    ]


def test_discovery_keeps_a_second_table_s_pointers_and_drops_the_strays(registry):
    """A file may hold the same table twice, and a packed write that rewrites
    one and not the other leaves the second stale: a string keeps its hits
    inside any run that reads as a table, and loses only the value that turns
    up alone."""
    rom = bytearray(b"\xff" * 0x100)
    rom[0:6] = bytes.fromhex("10 00 13 00 15 00")
    rom[0x40:0x46] = bytes.fromhex("10 00 13 00 15 00")
    rom[0x80:0x82] = bytes.fromhex("13 00")  # the same value, alone, in code
    rom[0x10:0x17] = bytes.fromhex("41 42 00 43 00 41 00")
    mappings = {"linear": resolve_mapping(registry, "linear")}
    starts = [0x10, 0x13, 0x15]
    best = discover(bytes(rom), starts, mappings, sizes=(2,), offsets=(0,))[0]
    assert best.table_run() == [0, 2, 4]
    refs = best.refs()
    assert [p.address for p in refs[0x10]] == [0, 0x40]
    assert [p.address for p in refs[0x13]] == [2, 0x42]
    assert [p.address for p in refs[0x15]] == [4, 0x44]


def test_discovery_keeps_every_hit_of_a_string_the_run_does_not_reach(registry):
    """Pointers scattered rather than tabulated are what **Attach** is for."""
    rom = bytearray(b"\xff" * 0x100)
    rom[0:6] = bytes.fromhex("10 00 13 00 15 00")
    rom[0x10:0x19] = bytes.fromhex("41 42 00 43 00 41 00 42 00")
    rom[0x80:0x82] = bytes.fromhex("17 00")
    rom[0x90:0x92] = bytes.fromhex("17 00")
    mappings = {"linear": resolve_mapping(registry, "linear")}
    starts = [0x10, 0x13, 0x15, 0x17]
    best = discover(bytes(rom), starts, mappings, sizes=(2,), offsets=(0,))[0]
    assert best.table_run() == [0, 2, 4]
    assert [p.address for p in best.refs()[0x17]] == [0x80, 0x90]


def test_discovery_gives_a_shared_address_to_the_string_in_the_table_s_bank(registry):
    """Two strings at the same address in different banks are reached by the
    same pointer value, so they share every address it is found at. The table
    is read in one bank — its own — and the addresses are that bank's."""
    rom = bytearray(b"\xff" * 0x18000)
    rom[0x8010:0x8015] = bytes.fromhex("41 42 00 43 00")
    rom[0x10010:0x10015] = bytes.fromhex("41 42 00 43 00")
    rom[0x10100:0x10104] = bytes.fromhex("10 80 13 80")
    mappings = {"lorom": resolve_mapping(registry, "lorom")}
    starts = [0x8010, 0x8013, 0x10010, 0x10013]
    best = discover(bytes(rom), starts, mappings, sizes=(2,), offsets=(0,))[0]
    assert best.addresses == [0x10100, 0x10102]
    assert best.stride == 2
    assert best.bank() == 2
    assert sorted(best.refs()) == [0x10010, 0x10013]
    src = best.source()
    assert src == PointerTableSource(0x10100, 0x10104, 2, 2, "little", "lorom", 0, 2)
    ex = extract(bytes(rom), BlockConfig(src, EndToken(), "main"), TS, registry)
    assert texts(ex) == ["AB[end]", "C[end]"]
    assert [s.start for s in ex.strings] == [0x10010, 0x10013]


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
    ends = string_ends(NESTED_ROM, cfg, ex.strings, registry, TS)
    assert ends == {0: 0x18, 1: 0x20, 2: 0x2C}
    res, out = relayout(NESTED_ROM, cfg, TS, {1: "CCCCC[end]"}, registry)
    assert res.ok and out[0x18:0x20] == bytes.fromhex("43 43 43 43 43 00 FF FF")
    assert out[:0x18] == NESTED_ROM[:0x18]
    # Packed, a string's room is its own bytes and its group's spare: map 0's
    # two strings hold five bytes of the eleven up to $20, so each may grow by
    # six — and only one of them may, the spare being the same six bytes.
    packed = BlockConfig(NESTED, EndToken(), "main")
    assert string_ends(NESTED_ROM, packed, ex.strings, registry, TS) == {
        0: 0x1E,
        1: 0x20,
        2: 0x2C,
    }


def test_a_nested_source_s_outer_pointers_say_which_of_the_pair_they_are(registry):
    cells = pointer_cells(NESTED_ROM, NESTED, 0, 0x30, registry)
    # The outer table's pointers come in pairs and reach structure, not text;
    # every inner pointer reaches a string and carries no role.
    assert [(c.address, c.role) for c in cells] == [
        (0x0, "table"),
        (0x2, "base"),
        (0x4, "table"),
        (0x6, "base"),
        (0x8, "table"),
        (0xA, "base"),
        (0x10, None),
        (0x12, None),
        (0x20, None),
        (0x22, None),
    ]
    # A pair half in view keeps its own role rather than the first one's.
    assert [(c.address, c.role) for c in pointer_cells(NESTED_ROM, NESTED, 2, 5)] == [
        (0x2, "base"),
        (0x4, "table"),
    ]


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


def test_a_pointer_block_with_no_bound_takes_back_the_fill_it_left(registry):
    """Room a shorter string gave up is there for a longer one afterwards: with
    no bound of its own the block's room ends after the fill behind its text,
    not where the text happens to end now."""
    rom = pointer_rom((0x10, 0x14), "41 42 43 00 43 00", tail=0) + b"\x01"
    cfg = BlockConfig(
        PointerTableSource(0, 4, 2, 2, "little", "linear", 0), EndToken(), "main"
    )
    res, out = relayout(rom, cfg, TS, {0: "A[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x10:0x17] == bytes.fromhex("41 00 43 00 FF FF 01")
    res, out = relayout(out, cfg, TS, {0: "ABC[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x10:0x17] == bytes.fromhex("41 42 43 00 43 00 01")
    # The byte after the fill is not the block's: one more does not fit.
    res, _ = relayout(out, cfg, TS, {0: "ABCA[end]"}, registry)
    assert not res.ok


def test_a_packed_write_keeps_a_banked_short_pointer_short(registry):
    """A 16-bit Game Boy pointer holds the address and the block the bank: a
    write rewrites the address, and refuses a string moved out of that bank."""
    body = bytes.fromhex("41 42 43 00 43 00")
    rom = bytearray(b"\xff" * 0x8020)
    rom[0x8000:0x8004] = bytes.fromhex("10 40 14 40")
    rom[0x8010 : 0x8010 + len(body)] = body
    rom[0x8016] = 0x01
    cfg = BlockConfig(
        PointerTableSource(0x8000, 0x8004, 2, 2, "little", "gb", 0, 2),
        EndToken(),
        "main",
    )
    assert texts(extract(bytes(rom), cfg, TS, registry)) == ["ABC[end]", "C[end]"]
    res, out = relayout(bytes(rom), cfg, TS, {0: "A[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x8000:0x8004] == bytes.fromhex("10 40 12 40")
    assert texts(extract(out, cfg, TS, registry)) == ["A[end]", "C[end]"]


FILL_IS_TEXT = table_set("@table main\n41=A\n42=B\n43=C\nFF=D\n/00=[end]\n", "main")
"""The abc table with the fill byte mapped: ``FF`` reads as a letter here."""


def test_a_nested_group_takes_its_fill_back_whatever_the_table_maps(registry):
    """What bounds a group is its outer table, not its reading of the fill.

    A block's own bound stops where the fill reads as text, since what lies
    past its last string is anyone's; a group's stops at the next inner table
    or base the outer table names, which says the stretch in front of it is
    this group's — so the room a shortening gave up is the group's to take
    back even in a script whose codes begin with the fill byte.
    """
    cfg = BlockConfig(NESTED, EndToken(), "main")
    ex = extract(NESTED_ROM, cfg, FILL_IS_TEXT, registry)
    assert texts(ex) == ["AB[end]", "C[end]", "A[end]"]
    for tables in (FILL_IS_TEXT, TS):
        assert string_ends(NESTED_ROM, cfg, ex.strings, registry, tables) == {
            0: 0x1E,
            1: 0x20,
            2: 0x2C,
        }
    res, out = relayout(NESTED_ROM, cfg, FILL_IS_TEXT, {0: "[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x14:0x20] == bytes.fromhex("FF 00 43 00 FF FF FF FF FF FF FF FF")
    res, back = relayout(out, cfg, FILL_IS_TEXT, {0: "AB[end]"}, registry)
    assert res.ok, res.problems
    assert back == NESTED_ROM
    # And no further: the next map's table is not the group's to write over.
    res, _ = relayout(NESTED_ROM, cfg, FILL_IS_TEXT, {1: "CCCCCCCCC[end]"}, registry)
    assert not res.ok and "bound" in res.problems[0].message
