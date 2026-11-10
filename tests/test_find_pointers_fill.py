"""Fill read as relative pointers (:func:`mapchar.engines.pointers._is_fill`):
a stretch repeating a pattern of up to eight bytes, four times and eight
bytes at least, reaches whatever lies that far past every address of it, so
a run of strings after it looks like a table at the strings' own spacing.
Such a stretch is no table; a real one, whatever values its pointers repeat,
keeps every pointer — and a mapping that raises loses only its own reading
while a fault in the search's own code is not hidden."""

from __future__ import annotations

import random

import pytest

from helpers import ASCII_TABLE, table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource, WriteMode
from mapchar.core.mapping import pointer_bytes
from mapchar.engines import pointers as engine
from mapchar.engines.pointers import discover
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import (
    apply_splices,
    layout_block,
    record_spans,
    record_start,
)
from mapchar.plugins.base import PluginInfo, Stage
from mapchar.plugins.registry import default_registry, resolve_mapping

REG = default_registry()
SOME = {
    mid: resolve_mapping(REG, mid)
    for mid in ("linear", "lorom", "relative", "relative-signed")
}
SIGNED = {"relative-signed": resolve_mapping(REG, "relative-signed")}
ASCII = table_set(ASCII_TABLE, "main")


def _fill_rom(pattern: bytes, stop: int = 0x18000, spacing: int = 8):
    """128 KiB of noise, ``pattern`` repeated from $8000 to ``stop``, twenty
    strings ``spacing`` bytes apart from $18000, and their table of two-byte
    little-endian LoROM pointers at $100: the bytes, the strings and their
    bytes."""
    rom = bytearray(random.Random(3).randbytes(0x20000))
    n = stop - 0x8000
    rom[0x8000:stop] = (pattern * (n // len(pattern) + 1))[:n]
    starts = [0x18000 + spacing * i for i in range(20)]
    for i, at in enumerate(starts):
        rom[at : at + spacing] = b"ABCDEFGHIJKLMNOP"[: spacing - 1] + b"\x00"
        rom[0x100 + 2 * i : 0x102 + 2 * i] = pointer_bytes(
            0x8000 | at & 0x7FFF, 2, "little"
        )
    return bytes(rom), starts, {s: [(s, s + spacing)] for s in starts}


def _in_fill(cands, stop: int) -> list:
    """Relative hits wholly inside the fill: those straddling its end are a
    value made of its last bytes and the first string's, not fill."""
    return [
        (c.mapping_id, c.size, c.endian, hex(a))
        for c in cands
        if c.relative
        for found in c.hits.values()
        for a in found
        if 0x8000 <= a and a + c.size <= stop
    ]


FILLS = [
    "00 01",
    "00 FF",
    "00 00 01 02",
    "00 00 FF FF",
    "FF FF 00 00",
    "FC FF 12 34",
    "FF",
    "00",
    "20 20",
    "01 02 03",
    "01 02 03 04 05 06 07",
]


@pytest.mark.parametrize("stop", [0x18000, 0x17FFD])
@pytest.mark.parametrize("pattern", FILLS)
def test_fill_is_not_a_table_of_the_strings_after_it(pattern, stop):
    """The real LoROM table stays first, and no relative hit lies inside the
    fill. ``FF FF 00 00`` and ``00`` ending at $18000 leave one hit at
    $17FFF, which straddles the fill's last byte and the first string's 'A':
    $0041 reaches the string at $18040 — a value of two different stretches,
    so no stretch holds it."""
    rom, starts, spans = _fill_rom(bytes.fromhex(pattern), stop)
    cands = discover(rom, starts, SOME, spans=spans)
    assert (cands[0].mapping_id, cands[0].size, cands[0].endian) == (
        "lorom",
        2,
        "little",
    )
    assert cands[0].table_run()[0] == 0x100
    assert _in_fill(cands, stop) == []


def test_without_the_rule_the_fill_wins():
    keep = engine._is_fill
    engine._is_fill = lambda *args: False
    try:
        lost = []
        for pattern in FILLS[:6]:
            rom, starts, spans = _fill_rom(bytes.fromhex(pattern))
            best = discover(rom, starts, SOME, spans=spans)[0]
            lost.append(best.relative)
    finally:
        engine._is_fill = keep
    assert all(lost)


def test_an_eight_byte_pattern_is_fill_unless_the_strings_are_a_period_apart():
    """Strings twelve bytes apart: fill. Eight apart, its period, every word
    one period on reaches the next string, so the fill is not recognised —
    one of the rule's stated losses."""
    pattern = bytes(range(1, 9))
    rom, starts, spans = _fill_rom(pattern, spacing=12)
    cands = discover(rom, starts, SOME, spans=spans)
    assert cands[0].mapping_id == "lorom" and _in_fill(cands, 0x18000) == []
    rom, starts, spans = _fill_rom(pattern, spacing=8)
    cands = discover(rom, starts, SOME, spans=spans)
    assert cands[0].relative and _in_fill(cands, 0x18000)


def test_a_table_repeating_one_value_three_times_keeps_every_pointer():
    """Words $0040 $0042 $0044 $0046 $0048 $0048 $0048 reach seven strings,
    the block holding the first five, so the two equal words after "E"'s
    reach none of its strings. Attached and rewritten after the first string
    shrinks, the table found still reads every one of them."""
    rom = bytearray(b"\xff" * 0x60)
    at, strings = 0x40, []
    for text in ("ONE", "TWO", "SIX", "TEN", "E", "X", "OTHER"):
        strings.append(at)
        rom[at : at + len(text) + 1] = text.encode() + b"\x00"
        at += len(text) + 1
    for i, string in enumerate(strings):
        rom[2 * i : 2 * i + 2] = pointer_bytes(string - 2 * i, 2, "big")
    assert rom[0:14] == bytes.fromhex("0040 0042 0044 0046 0048 0048 0048")
    cfg = BlockConfig(
        RangeSource(0x40, 0x52), EndToken(), "main", write_mode=WriteMode.PACKED
    )
    ex = extract(bytes(rom), cfg, ASCII, REG)
    spans = {record_start(r, cfg): record_spans(r, cfg) for r in ex.strings}
    cands = discover(bytes(rom), [r.start for r in ex.strings], SIGNED, spans=spans)
    best = cands[0]
    refs = best.refs()
    assert [refs[s][0].address for s in strings[:5]] == [0, 2, 4, 6, 8]
    for rec in ex.strings:
        rec.pointers = refs.get(rec.start, ())
    ex.strings[0].replacement = "1[end]"
    res = layout_block(bytes(rom), cfg, ASCII, ex.strings, REG)
    assert res.ok, res.problems
    out = apply_splices(bytes(rom), res.splices)
    table = BlockConfig(best.source(), EndToken(), "main")
    read = [r.original_text() for r in extract(out, table, ASCII, REG).strings]
    assert read == ["1[end]", "TWO[end]", "SIX[end]", "TEN[end]", "E[end]"]
    assert out[0x52:0x5A] == b"X\x00OTHER\x00"


def test_a_word_beside_a_table_equal_to_its_end_pointer_drops_nothing():
    rom = bytearray(random.Random(8).randbytes(0x80))
    strings = [0x40, 0x48, 0x50, 0x58]
    for at in strings:
        rom[at : at + 8] = b"ABCDEFG\x00"
    for i, string in enumerate(strings):
        rom[0x10 + 2 * i : 0x12 + 2 * i] = pointer_bytes(
            string - 0x10 - 2 * i, 2, "big"
        )
    rom[0x0E:0x10] = rom[0x10:0x12]  # the word before equals the first pointer
    rom[0x18:0x1A] = rom[0x16:0x18]  # the word after equals the last
    spans = {s: [(s, s + 8)] for s in strings}
    best = discover(bytes(rom), strings, SIGNED, sizes=(2,), spans=spans)[0]
    assert {s: best.hits[s] for s in strings} == {
        0x40: [0x10],
        0x48: [0x12],
        0x50: [0x14],
        0x58: [0x16],
    }


def test_one_string_whose_pointer_has_two_equal_neighbours_is_found():
    """Three strings two bytes apart, pointers two bytes apart: three equal
    words, six bytes, too short to be fill."""
    rom = bytearray(b"\xff" * 0x40)
    for i, string in enumerate((0x20, 0x22, 0x24)):
        rom[string : string + 2] = b"A\x00"
        rom[2 * i : 2 * i + 2] = pointer_bytes(string - 2 * i, 2, "big")
    cands = discover(bytes(rom), [0x22], SIGNED, sizes=(2,), endians=("big",))
    assert cands[0].hits == {0x22: [0x02]}


def _holes(rng: random.Random) -> int:
    """A random table of sixteen one-byte signed pointers in two-byte
    records, its strings after it: how many of its pointers the search
    misses."""
    rom = bytearray(rng.randbytes(0x100))
    at, strings = 0x20, []
    for _ in range(16):
        strings.append(at)
        n = rng.randrange(0, 6)
        rom[at : at + n + 1] = bytes(rng.randrange(1, 256) for _ in range(n)) + b"\0"
        at += n + 1
    for i, string in enumerate(strings):
        rom[2 * i] = (string - 2 * i) & 0xFF
    ends = [*strings[1:], at]
    spans = {s: [(s, end)] for s, end in zip(strings, ends, strict=True)}
    cands = discover(
        bytes(rom), strings, SIGNED, sizes=(1,), endians=("little",), spans=spans
    )
    found = {a for c in cands for hits in c.hits.values() for a in hits}
    return sum(2 * i not in found for i in range(16))


def test_one_byte_pointers_in_two_byte_records_lose_no_more_than_without_it():
    on = sum(_holes(random.Random(n)) for n in range(300))
    keep = engine._is_fill
    engine._is_fill = lambda *args: False
    try:
        off = sum(_holes(random.Random(n)) for n in range(300))
    finally:
        engine._is_fill = keep
    assert on <= off


class Raising:
    """A mapping whose every call fails."""

    info = PluginInfo("raising", "Raising", Stage.MAPPING)
    sizes = (2,)
    needs_bank = False

    def to_offset(self, value, bank=0, ptr_address=0):
        raise RuntimeError("to_offset")

    def to_value(self, offset, bank=0, ptr_address=0):
        raise RuntimeError("to_value")


class RaisingRelative(Raising):
    relative = True


class Speechless:
    """A mapping that answers no number."""

    info = PluginInfo("speechless", "Speechless", Stage.MAPPING)
    sizes = (2,)
    needs_bank = False

    def to_offset(self, value, bank=0, ptr_address=0):
        return value

    def to_value(self, offset, bank=0, ptr_address=0):
        return None


class Bankless(Speechless):
    needs_bank = True

    def to_value(self, offset, bank=0, ptr_address=0):
        return offset

    def bank_of(self, offset):
        return None


def test_a_mapping_that_raises_loses_only_its_own_reading():
    rom = bytearray(b"\xff" * 0x40)
    rom[0x20:0x23] = b"AB\x00"
    rom[0x00:0x02] = pointer_bytes(0x20, 2, "little")
    mappings = {
        "raising": Raising(),
        "raising-relative": RaisingRelative(),
        "speechless": Speechless(),
        "bankless": Bankless(),
        "linear": resolve_mapping(REG, "linear"),
    }
    cands = discover(bytes(rom), [0x20], mappings, sizes=(2,))
    assert {c.mapping_id for c in cands} == {"linear"}


def test_a_fault_in_the_search_itself_is_not_hidden(monkeypatch):
    rom = bytearray(b"\xff" * 0x40)
    rom[0x20:0x23] = b"AB\x00"
    rom[0x00:0x02] = pointer_bytes(0x20, 2, "little")

    def broken(*args):
        raise RuntimeError("the search's own fault")

    monkeypatch.setattr(engine, "_overlaps", broken)
    with pytest.raises(RuntimeError):
        discover(bytes(rom), [0x20], SIGNED, sizes=(2,))
    monkeypatch.undo()
    with pytest.raises((TypeError, ValueError)):
        discover(bytes(rom), [0x20], SIGNED, sizes=(2,), spans={0x20: [(0x20,)]})
    with pytest.raises((TypeError, ValueError)):
        linear = {"linear": resolve_mapping(REG, "linear")}
        discover(bytes(rom), [0x20], linear, sizes=(2,), spans={0x20: [(0x20,)]})
