"""The relative pointer mappings: ``relative``, an unsigned distance from the
pointer, and ``relative-signed``, a two's-complement one at the pointer's own
size — the 68000's ``lea d16(pc)`` — which reaches strings on both sides of
it from one block, writes back on both sides and refuses a distance its size
cannot hold, and which pointer discovery finds a table of."""

from __future__ import annotations

import pytest

from helpers import ABC_TABLE, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    PointerListSource,
    PointerTableSource,
)
from mapchar.core.mapping import (
    holds,
    pointer_bytes,
    pointer_offset,
    sign_extend,
)
from mapchar.engines.pointers import discover
from mapchar.pipeline.extract import extract
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import default_registry, resolve_mapping

TS = table_set(ABC_TABLE, "main")
REG = default_registry()
SIGNED = resolve_mapping(REG, "relative-signed")
RELATIVE = resolve_mapping(REG, "relative")


def _word(value: int) -> bytes:
    """A big-endian 16-bit pointer, a negative one as its two's complement."""
    return pointer_bytes(value, 2, "big")


def _two_sided() -> bytes:
    """``AB[end]`` at $00, a table of three big-endian signed pointers at $20,
    and ``C[end]`` and ``AAA[end]`` at $30: the first pointer reaches back,
    the other two forward."""
    rom = bytearray(b"\xff" * 0x40)
    rom[0x00:0x03] = bytes.fromhex("41 42 00")
    rom[0x20:0x26] = _word(0x00 - 0x20) + _word(0x30 - 0x22) + _word(0x32 - 0x24)
    rom[0x30:0x36] = bytes.fromhex("43 00 41 41 41 00")
    return bytes(rom)


def _table(mapping: str = "relative-signed", offset: int = 0) -> BlockConfig:
    return BlockConfig(
        PointerTableSource(0x20, 0x26, 2, 2, "big", mapping, offset),
        EndToken(),
        "main",
    )


def test_both_are_registered_and_only_the_new_one_is_signed():
    ids = REG.ids(Stage.MAPPING)
    assert "relative" in ids and "relative-signed" in ids
    assert SIGNED.sizes == RELATIVE.sizes == (1, 2, 3, 4)
    assert not SIGNED.needs_bank and SIGNED.relative and RELATIVE.relative
    assert SIGNED.signed and not getattr(RELATIVE, "signed", False)


def test_a_value_is_sign_extended_at_the_pointer_s_own_size():
    assert sign_extend(0xFF, 1) == -1 and sign_extend(0x7F, 1) == 0x7F
    assert sign_extend(0xFFFE, 2) == -2 and sign_extend(0x8000, 2) == -0x8000
    assert sign_extend(0x800000, 3) == -0x800000
    assert sign_extend(0xFFFFFFFF, 4) == -1
    assert pointer_offset(SIGNED, 0xFFF0, 2, 0, 0x100) == 0xF0
    assert pointer_offset(SIGNED, 0x00F0, 2, 0, 0x100) == 0x1F0
    # The unsigned mapping reads the same bytes as the distance they spell.
    assert pointer_offset(RELATIVE, 0xFFF0, 2, 0, 0x100) == 0x100 + 0xFFF0


def test_every_size_round_trips_both_ways():
    for size in (1, 2, 3, 4):
        top = (1 << (size * 8 - 1)) - 1
        for distance in (-top - 1, -5, 0, 7, top):
            at = 0x1000000
            target = at + distance
            value = SIGNED.to_value(target, 0, at)
            assert holds(value, size, True)
            raw = int.from_bytes(pointer_bytes(value, size, "little"), "little")
            assert pointer_offset(SIGNED, raw, size, 0, at) == target, (size, distance)
        assert not holds(top + 1, size, True) and not holds(-top - 2, size, True)


def test_one_block_reads_strings_before_and_after_its_table():
    ex = extract(_two_sided(), _table(), TS, REG)
    assert texts(ex) == ["AB[end]", "C[end]", "AAA[end]"]
    assert [s.start for s in ex.strings] == [0x00, 0x30, 0x32]
    # The value a string's pointer holds is the bytes, read unsigned.
    assert ex.strings[0].pointers[0].value == 0xFFE0


def test_the_unsigned_mapping_cannot_reach_back_from_the_same_table():
    ex = extract(_two_sided(), _table("relative"), TS, REG)
    assert texts(ex) == ["C[end]", "AAA[end]"]
    assert any("maps outside" in n.message for n in ex.notices)


def test_a_packed_write_moves_strings_on_both_sides_of_their_pointers():
    """Pointers at $00 and $30 reach strings packed between them: one reaches
    forward, one back, and the write keeps both reaching their strings."""
    rom = bytearray(b"\xff" * 0x40)
    rom[0x00:0x02] = _word(0x10 - 0x00)
    rom[0x30:0x32] = _word(0x13 - 0x30)
    rom[0x10:0x15] = bytes.fromhex("41 42 00 43 00")
    cfg = BlockConfig(
        PointerListSource((0x00, 0x30), 2, "big", "relative-signed"),
        EndToken(),
        "main",
        bound=0x20,
        fill=b"\xee",
    )
    res, out = relayout(bytes(rom), cfg, TS, {0: "ABCAB[end]"}, REG)
    assert res.ok, res.problems
    assert out[0x10:0x18] == bytes.fromhex("41 42 43 41 42 00 43 00")
    assert out[0x00:0x02] == _word(0x10)
    # Now 0x16 - 0x30 = -$1A, written as its two's complement.
    assert out[0x30:0x32] == bytes.fromhex("FF E6")
    assert texts(extract(out, cfg, TS, REG)) == ["ABCAB[end]", "C[end]"]


def test_a_distance_past_the_signed_range_is_refused():
    """A one-byte pointer reaches $7F ahead at most; the unsigned mapping
    holds $80 in the same byte."""
    rom = bytearray(b"\xff" * 0x90)
    rom[0x00] = 0x7C
    rom[0x01] = 0x7F - 0x01
    rom[0x7C:0x81] = bytes.fromhex("41 42 00 43 00")

    def cfg(mapping: str) -> BlockConfig:
        return BlockConfig(
            PointerListSource((0x00, 0x01), 1, "little", mapping),
            EndToken(),
            "main",
            bound=0x90,
            fill=b"\xee",
        )

    assert texts(extract(bytes(rom), cfg("relative-signed"), TS, REG)) == [
        "AB[end]",
        "C[end]",
    ]
    res, _ = relayout(bytes(rom), cfg("relative-signed"), TS, {0: "ABCC[end]"}, REG)
    assert not res.ok
    assert [p.message for p in res.problems] == ["pointer at $1 cannot hold $80"]
    res, out = relayout(bytes(rom), cfg("relative"), TS, {0: "ABCC[end]"}, REG)
    assert res.ok, res.problems
    assert out[0x01] == 0x80


def test_relative_with_a_negative_offset_reads_and_writes_as_before():
    """The old way to reach back: unsigned values and ``offset=-65536``."""
    rom = bytearray(b"\xff" * 0x40)
    rom[0x20:0x24] = _word(0x10000 + 0x00 - 0x20) + _word(0x10000 + 0x03 - 0x22)
    rom[0x00:0x05] = bytes.fromhex("41 42 00 43 00")
    cfg = BlockConfig(
        PointerTableSource(0x20, 0x24, 2, 2, "big", "relative", -0x10000),
        EndToken(),
        "main",
        bound=0x10,
        fill=b"\xee",
    )
    assert texts(extract(bytes(rom), cfg, TS, REG)) == ["AB[end]", "C[end]"]
    res, out = relayout(bytes(rom), cfg, TS, {0: "ABC[end]"}, REG)
    assert res.ok, res.problems
    assert out[0x00:0x06] == bytes.fromhex("41 42 43 00 43 00")
    assert out[0x20:0x24] == bytes.fromhex("FF E0 FF E2")
    assert texts(extract(out, cfg, TS, REG)) == ["ABC[end]", "C[end]"]


def test_discovery_finds_a_table_of_signed_relative_pointers():
    rom = _two_sided()
    mappings = {
        mid: resolve_mapping(REG, mid)
        for mid in ("linear", "relative", "relative-signed")
    }
    cands = discover(rom, [0x00, 0x30, 0x32], mappings, sizes=(2,))
    best = cands[0]
    assert (best.mapping_id, best.size, best.endian) == ("relative-signed", 2, "big")
    assert best.explained == 3 and best.stride == 2
    assert best.source() == PointerTableSource(
        0x20, 0x26, 2, 2, "big", "relative-signed", 0
    )
    refs = best.refs()
    # Each pointer holds its own distance, not one value per string.
    assert refs[0x00][0].value == 0xFFE0 and refs[0x30][0].value == 0x000E
    unsigned = next(c for c in cands if c.mapping_id == "relative")
    assert unsigned.explained == 2


@pytest.mark.parametrize(
    "size, endian", [(3, "little"), (3, "big"), (4, "little"), (4, "big")]
)
def test_three_and_four_byte_pointers_read_and_write_both_ways(size, endian):
    """One pointer ahead of the strings, one behind them, in either byte
    order: the one behind holds a negative distance in every byte."""
    rom = bytearray(b"\xff" * 0x50)
    rom[0x00:size] = pointer_bytes(0x10 - 0x00, size, endian)
    rom[0x40 : 0x40 + size] = pointer_bytes(0x13 - 0x40, size, endian)
    rom[0x10:0x15] = bytes.fromhex("41 42 00 43 00")
    cfg = BlockConfig(
        PointerListSource((0x00, 0x40), size, endian, "relative-signed"),
        EndToken(),
        "main",
        bound=0x20,
        fill=b"\xee",
    )
    assert texts(extract(bytes(rom), cfg, TS, REG)) == ["AB[end]", "C[end]"]
    res, out = relayout(bytes(rom), cfg, TS, {0: "ABCAB[end]"}, REG)
    assert res.ok, res.problems
    assert out[0x00:size] == rom[0x00:size]
    back = {
        (3, "little"): "D6 FF FF",
        (3, "big"): "FF FF D6",
        (4, "little"): "D6 FF FF FF",
        (4, "big"): "FF FF FF D6",
    }[size, endian]
    assert out[0x40 : 0x40 + size] == bytes.fromhex(back)  # 0x16 - 0x40 = -$2A
    assert texts(extract(out, cfg, TS, REG)) == ["ABCAB[end]", "C[end]"]


def test_an_offset_record_bytes_and_a_shared_target_with_signed_pointers():
    """Records of a pointer and two bytes of something else, read with an
    offset of 2; two of them reach one string, each from its own address."""
    rom = bytearray(b"\xff" * 0x60)
    rom[0x10:0x15] = bytes.fromhex("41 42 00 43 00")
    for at, string in ((0x40, 0x10), (0x44, 0x13), (0x48, 0x10)):
        rom[at : at + 4] = _word(string - 2 - at) + b"\xaa\xbb"
    cfg = BlockConfig(
        PointerTableSource(0x40, 0x4C, 2, 4, "big", "relative-signed", 2),
        EndToken(),
        "main",
        bound=0x20,
        fill=b"\xee",
    )
    ex = extract(bytes(rom), cfg, TS, REG)
    assert texts(ex) == ["AB[end]", "C[end]"]
    assert [p.address for p in ex.strings[0].pointers] == [0x40, 0x48]
    res, out = relayout(bytes(rom), cfg, TS, {0: "ABC[end]"}, REG)
    assert res.ok, res.problems
    # "C" moves to $14; "AB" stays, so its two pointers keep their values.
    assert out[0x40:0x4C] == (
        _word(0x10 - 2 - 0x40)
        + b"\xaa\xbb"
        + _word(0x14 - 2 - 0x44)
        + b"\xaa\xbb"
        + _word(0x10 - 2 - 0x48)
        + b"\xaa\xbb"
    )
    assert out[0x40:0x42] != out[0x48:0x4A]
    assert texts(extract(out, cfg, TS, REG)) == ["ABC[end]", "C[end]"]


def test_a_negative_distance_past_the_range_is_refused_with_its_sign():
    """Shortening the first string moves the second back, out of reach of a
    one-byte pointer $7F behind it."""
    rom = bytearray(b"\xff" * 0xA0)
    rom[0x10:0x15] = bytes.fromhex("41 42 00 43 00")
    rom[0x90] = (0x10 - 0x90) & 0xFF
    rom[0x92] = (0x13 - 0x92) & 0xFF
    cfg = BlockConfig(
        PointerListSource((0x90, 0x92), 1, "little", "relative-signed"),
        EndToken(),
        "main",
        bound=0x20,
        fill=b"\xee",
    )
    assert texts(extract(bytes(rom), cfg, TS, REG)) == ["AB[end]", "C[end]"]
    res, out = relayout(bytes(rom), cfg, TS, {0: "A[end]"}, REG)
    assert res.ok, res.problems
    assert out[0x92] == 0x80  # -$80, the furthest back a byte reaches
    res, _ = relayout(bytes(rom), cfg, TS, {0: "[end]"}, REG)
    assert [p.message for p in res.problems] == ["pointer at $92 cannot hold -$81"]
