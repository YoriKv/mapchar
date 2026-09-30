"""Combining finished captures (:mod:`mapchar.capture.combine`) on synthetic
results: how strings end, what a code means when captures disagree, pointer
slots and the tables they make — wider pointers, pointers in code, split
pointers, nulls and shared targets — and engines."""

from __future__ import annotations

from dataclasses import replace

from capture_fake import CONSOLE
from mapchar.capture import consoles
from mapchar.capture.combine import (
    HOLDS,
    Sighting,
    _bounds,
    _mapping_for,
    combine,
)
from mapchar.capture.trace import Code, Pointer, Result, Source
from mapchar.plugins.registry import default_registry, resolve_mapping

REG = default_registry()
MAPPINGS = {
    i: resolve_mapping(REG, i)
    for i in ("linear", "lorom", "hirom", "exhirom", "sa1", "gba")
}
READ = "read before"


def rom_of(size=0x1000, fill=0xEE) -> bytearray:
    return bytearray([fill] * size)


def result(start, end, *, output="vram", reader=0x9000, **kw) -> Result:
    return Result(output=output, reader=reader, string=(start, end), **kw)


def sight(cap, r) -> Sighting:
    return Sighting(cap, "", r)


def slot(rom, at, target, size=2, endian="little"):
    """A pointer to ``target`` at ``at``, and the tracer's report of it."""
    rom[at : at + size] = target.to_bytes(size, endian)
    lo, hi = (at, at + 1) if endian == "little" else (at + 1, at)
    return [Pointer(lo, 2, READ), Pointer(hi, 512, READ)]


# -- how strings end


def test_two_strings_are_no_record_size():
    import random

    rng = random.Random(2)
    rom = bytearray(rng.randrange(1, 255) for _ in range(0x6000))
    ss = [sight("a", result(0x1000, 0x1009)), sight("b", result(0x1040, 0x104B))]
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.fixed_length is None
    assert any("not known" in n for n in e.notes)
    # Two strings far apart are no record, whatever lies between them.
    far = [sight("a", result(0x1000, 0x1009)), sight("b", result(0x5000, 0x500B))]
    (e,) = combine(far, bytes(rom_of(0x6000)), CONSOLE, MAPPINGS).engines
    assert e.fixed_length is None


def test_two_padded_records_are_a_fixed_length():
    # EarthBound: 40-byte name prompts padded with $00.
    rom = rom_of(0x1000, 0x00)
    for at in (0x194, 0x194 + 40):
        rom[at : at + 12] = b"Please name "
    ss = [
        sight("a", result(0x194, 0x194 + 11)),
        sight("b", result(0x194 + 40, 0x194 + 51)),
    ]
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.fixed_length == 40


def test_three_strings_a_record_apart_are_a_fixed_length():
    rom = rom_of(0x2000)
    ss = [
        sight("a", result(0x1000, 0x1009)),
        sight("b", result(0x1010, 0x101B)),
        sight("c", result(0x1030, 0x1035)),
    ]
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.fixed_length == 16
    assert any("fixed length of 16" in n for n in e.notes)


def test_a_wide_stride_is_a_record_only_when_padded():
    import random

    rng = random.Random(1)
    noisy = bytearray(rng.randrange(1, 255) for _ in range(0x4000))
    ss = [sight(c, result(0x80 * n, 0x80 * n + 9)) for n, c in enumerate("abc", 1)]
    (e,) = combine(ss, bytes(noisy), CONSOLE, MAPPINGS).engines
    assert e.fixed_length is None
    padded = rom_of(0x4000, 0x00)
    (e,) = combine(ss, bytes(padded), CONSOLE, MAPPINGS).engines
    assert e.fixed_length == 0x80


def test_a_table_s_strings_run_to_the_next_pointer():
    rom = rom_of()
    ss = []
    for n, cap in enumerate("abc"):
        target = 0x400 + 0x10 * n
        ss.append(
            sight(
                cap,
                result(target, target + 5, pointers=slot(rom, 0x200 + 2 * n, target)),
            )
        )
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.table is not None and e.fixed_length is None and e.end_code is None


def test_an_end_token_the_strings_agree_on():
    rom = rom_of()
    rom[0x105] = rom[0x205] = 0xFF
    codes = [Code(0xFF, "end")]
    ss = [
        sight("a", result(0x100, 0x105, codes=codes)),
        sight("b", result(0x200, 0x205, codes=codes)),
    ]
    c = combine(ss, bytes(rom), CONSOLE, MAPPINGS)
    assert c.engines[0].end_code == 0xFF and c.meanings["FF"].kind == "end"


# -- what a code means


def smw_line(tier="copied"):
    """A typed ``e`` whose ROM code, $C4, also ends its line (bit 7)."""
    rom = rom_of()
    src = 0x300
    rom[src] = 0xC4
    r = result(
        src,
        src,
        output="ram",
        reader=0x9010,
        typed=[("e", 0x44, src)],
        decoder={0x44: "e", 0x20: " "},
        sources=[Source(0, src, tier)],
        codes=[Code(0xC4, "line", [0x44])],
    )
    return rom, r


def test_a_typed_character_that_ends_its_line_keeps_the_line_break():
    rom, r = smw_line()
    c = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS)
    m = c.meanings["C4"]
    assert (m.text, m.typed) == ("e\\n", True)
    assert not c.conflicts


def test_a_typed_character_is_the_rom_code_only_of_a_source_it_follows():
    rom, r = smw_line("determines")
    r.codes = []
    c = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS)
    assert "C4" not in c.meanings
    # Translated through a lookup table: only this output changes with it.
    rom, r = smw_line("only-this")
    r.codes = []
    c = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS)
    assert c.meanings["C4"].text == "e" and c.meanings["C4"].typed


def test_a_typed_letter_outweighs_another_capture_s_alphabet():
    # The font skips a letter, so two captures read the run from different
    # bases: each typed letter is its own, and only codes neither matched
    # disagree.
    lower = "abcdefghijklmnopqrstuvwxyz"
    rom = bytes(0x1000)

    def capture(base, word, start):
        return result(
            start,
            start + len(word),
            decoder={base + i: ch for i, ch in enumerate(lower)},
            typed=[(ch, base + lower.index(ch), None) for ch in word],
        )

    ss = [
        sight("a", capture(0x3F, "rest", 0x100)),
        sight("b", capture(0x40, "hello", 0x200)),
    ]
    c = combine(ss, rom, CONSOLE, MAPPINGS)
    assert c.meanings["50"].text == "r" and c.meanings["50"].typed
    assert c.meanings["47"].text == "h"
    assert all(k.key not in ("50", "47", "44", "4B", "4E") for k in c.conflicts)
    assert c.conflicts  # codes only the alphabets read disagree


def test_a_typed_reading_against_another_is_a_conflict():
    rom, r = smw_line()
    r.codes = [Code(0xC4, "char", [0x45])]
    r.decoder[0x45] = "f"
    c = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS)
    (k,) = c.conflicts
    assert k.key == "C4" and set(k.readings) == {"text:e", "text:f"}
    assert k.choices["text:f"].text == "f"


def test_a_line_code_that_writes_nothing_is_a_glyph():
    rom, r = smw_line()
    r.codes = [Code(0x80, "line", [])]
    c = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS)
    assert [g.note for g in c.glyphs] == ["ends the line"]


def test_commands_are_compared_with_their_parameters():
    rom = rom_of()
    ss = [
        sight("a", result(0x100, 0x105, codes=[Code(0xFC, "command", skip=1)])),
        sight("b", result(0x200, 0x205, codes=[Code(0xFC, "command", skip=2)])),
    ]
    c = combine(ss, bytes(rom), CONSOLE, MAPPINGS)
    (k,) = c.conflicts
    assert len(k.readings) == 2
    assert sorted(m.params for m in k.choices.values()) == [1, 2]
    # Two captures agreeing are one meaning.
    ss[1].result.codes = [Code(0xFC, "command", skip=1)]
    c = combine(ss, bytes(rom), CONSOLE, MAPPINGS)
    assert c.meanings["FC"].params == 1 and c.meanings["FC"].captures == {"a", "b"}


def test_a_reader_that_jumps_far_is_no_parameter_count():
    rom = rom_of()
    r = result(0x100, 0x105, codes=[Code(0xFD, "command", skip=70000)])
    m = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS).meanings["FD"]
    assert m.params == 0 and not m.confirmed and "jumps +70000" in m.note


def test_a_code_where_a_space_was_typed_is_a_line_break_candidate():
    rom = rom_of()
    r = result(
        0x100,
        0x120,
        decoder={0x1F: " "},
        gaps=[(" ", [0x1F]), (" ", [0xF1]), (" ", [0x1F])],
        codes=[Code(0xF1, "command", skip=0), Code(0xF2, "printable", image="x")],
    )
    r.gaps.append((" ", [0xF2]))
    c = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS)
    assert not c.meanings["F1"].confirmed and "a line break?" in c.meanings["F1"].note
    (g,) = c.glyphs
    assert "a line break?" in g.note


# -- pointers and tables


def test_a_pointer_in_code_is_no_table():
    # EarthBound: an LDA # operand holds the string's address; the tracer
    # finds it by its value and moves both its bytes.
    rom = rom_of(0x20000)
    rom[0x1F94A:0x1F94C] = (0x4000).to_bytes(2, "little")
    held = [Pointer(0x1F94A, 2, HOLDS), Pointer(0x1F94B, 512, HOLDS)]
    r = result(0x4000, 0x4010, pointers=held)
    (e,) = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.table is None and e.single == {"a": [0x1F94A]}


def test_operands_scattered_through_code_are_no_table():
    # EarthBound's naming prompts: 40-byte records padded with $00, each
    # reached by an operand somewhere in the code.
    rom = rom_of(0x80000, 0x00)
    ops = [0x1F94A, 0x1F9AD, 0x1FA01, 0x1FA53]
    ss = []
    for n, op in enumerate(ops):
        target = 0x4C194 + 40 * n
        text = b"Please name him."[: 10 + n]
        rom[target : target + len(text)] = text
        rom[op - 1] = 0xA9
        rom[op : op + 2] = (target & 0xFFFF).to_bytes(2, "little")
        held = [Pointer(op, 2, HOLDS), Pointer(op + 1, 512, HOLDS)]
        ss.append(
            sight("abcd"[n], result(target, target + len(text) - 1, pointers=held))
        )
    (e,) = combine(ss, bytes(rom), consoles.SNES_HIROM, MAPPINGS).engines
    assert e.table is None and e.fixed_length == 40
    assert sorted(a for addrs in e.single.values() for a in addrs) == ops


def test_evenly_spaced_pointers_found_by_value_are_a_table():
    rom = rom_of()
    ss = []
    for n in range(3):
        target = 0x400 + 0x10 * n
        at = 0x200 + 2 * n
        rom[at : at + 2] = target.to_bytes(2, "little")
        held = [Pointer(at, 2, HOLDS), Pointer(at + 1, 512, HOLDS)]
        ss.append(sight("abc"[n], result(target, target + 5, pointers=held)))
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.table is not None and e.table.slots == [0x200, 0x202, 0x204]
    assert not e.single


def test_unsure_slots_two_captures_share_are_a_table():
    rom = rom_of()
    ss = []
    for n, cap in enumerate("ab"):
        target = 0x400 + 0x10 * n
        rom[0x200 + 2 * n : 0x202 + 2 * n] = target.to_bytes(2, "little")
        r = result(target, target + 5, pointers=[Pointer(0x200 + 2 * n, 2, READ)])
        ss.append(sight(cap, r))
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.table is not None and e.table.slots == [0x200, 0x202]
    # One capture's alone is no table, and says so.
    (e,) = combine(ss[:1], bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.table is None and e.loose == [0x200]


def test_a_big_endian_slot():
    rom = rom_of()
    r = result(0x400, 0x405, pointers=slot(rom, 0x200, 0x400, endian="big"))
    (e,) = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS).engines
    t = e.table
    assert (t.start, t.size, t.endian, t.mapping_id) == (0x200, 2, "big", "linear")


def test_a_snes_pointer_is_read_with_its_bank_byte():
    rom = rom_of(0x10000)
    target = 0x8123  # LoROM $01:8123
    pointers = slot(rom, 0x200, 0x018123, size=3)[:2]
    r = result(target, target + 5, pointers=pointers)
    (e,) = combine([sight("a", r)], bytes(rom), consoles.SNES_LOROM, MAPPINGS).engines
    t = e.table
    assert (t.start, t.size, t.mapping_id, t.offset) == (0x200, 3, "lorom", 0)


def test_a_gba_pointer_is_read_whole():
    rom = rom_of(0x2000)
    pointers = slot(rom, 0x100, 0x08001234, size=4)[:2]
    r = result(0x1234, 0x1240, pointers=pointers)
    (e,) = combine([sight("a", r)], bytes(rom), consoles.GBA, MAPPINGS).engines
    t = e.table
    assert (t.size, t.mapping_id, t.offset) == (4, "gba", 0)


def test_pointers_past_hirom_and_lorom_resolve():
    # ExHiROM: a 24-bit pointer into the second 4 MiB.
    rom = rom_of(0x600000)
    pointers = slot(rom, 0x200, 0x512340, size=3)[:2]
    r = result(0x512340, 0x512345, pointers=pointers)
    (e,) = combine([sight("a", r)], bytes(rom), consoles.SNES_EXHIROM, MAPPINGS).engines
    t = e.table
    assert (t.size, t.mapping_id, t.offset) == (3, "exhirom", 0)
    # SA-1: a 24-bit pointer into bank $80, the third MiB — no mirror.
    rom = rom_of(0x300000)
    pointers = slot(rom, 0x200, 0x808010, size=3)[:2]
    r = result(0x200010, 0x200015, pointers=pointers)
    (e,) = combine([sight("a", r)], bytes(rom), consoles.SNES_SA1, MAPPINGS).engines
    t = e.table
    assert (t.size, t.mapping_id, t.offset) == (3, "sa1", 0)
    # A 16-bit one there takes the bank the string is in.
    rom = rom_of(0x300000)
    r = result(0x200010, 0x200015, pointers=slot(rom, 0x200, 0x8010))
    (e,) = combine([sight("a", r)], bytes(rom), consoles.SNES_SA1, MAPPINGS).engines
    t = e.table
    assert (t.size, t.mapping_id, t.bank) == (2, "sa1", 0x40)


def test_a_zero_after_a_pointer_is_no_bank_byte():
    # LoROM records of a 16-bit pointer and two zero bytes, strings in bank 1.
    rom = rom_of(0x20000)
    ss = []
    for n in range(3):
        t = 0x8000 + 0x20 * n
        at = 0x200 + 4 * n
        pointers = slot(rom, at, 0x8000 | (t & 0x7FFF))
        rom[at + 2 : at + 4] = b"\x00\x00"
        ss.append(sight("abc"[n], result(t, t + 5, pointers=pointers)))
    (e,) = combine(ss, bytes(rom), consoles.SNES_LOROM, MAPPINGS).engines
    assert (e.table.size, e.table.stride) == (2, 4)
    # HiROM: a 16-bit table whose next pointer's low byte is $00.
    rom = rom_of(0x50000)
    for n, v in enumerate([0x4100, 0x4200, 0x4300]):
        rom[0x300 + 2 * n : 0x302 + 2 * n] = v.to_bytes(2, "little")
    r = result(0x44100, 0x44105, pointers=slot(rom, 0x300, 0x4100))
    (e,) = combine([sight("a", r)], bytes(rom), consoles.SNES_HIROM, MAPPINGS).engines
    assert (e.table.size, e.table.stride) == (2, 2)


def test_a_reading_is_wider_only_when_every_sure_slot_is():
    # Two captures of one 2-byte table: after one slot the next byte happens
    # to read as a bank byte, after the other it does not.
    rom = rom_of(0x20000)
    ss = []
    for n, bank_like in enumerate((0x01, 0x42)):
        t = 0x8000 + 0x20 * n
        at = 0x200 + 4 * n
        pointers = slot(rom, at, 0x8000 | (t & 0x7FFF))
        rom[at + 2] = bank_like
        ss.append(sight("ab"[n], result(t, t + 5, pointers=pointers)))
    (e,) = combine(ss, bytes(rom), consoles.SNES_LOROM, MAPPINGS).engines
    assert e.table.size == 2 and e.table.slots == [0x200, 0x204]


def test_mapping_for_tries_the_console_s_mappings_first():
    lorom = consoles.SNES_LOROM
    assert _mapping_for(0x8123, 0x8123, 2, lorom, MAPPINGS) == ("lorom", 0, 1)
    # A record header ahead of the string.
    assert _mapping_for(0x8123, 0x8127, 2, lorom, MAPPINGS) == ("lorom", 4, 1)
    # Nothing the console maps: a constant added to the value.
    assert _mapping_for(0x0123, 0x9000, 2, lorom, MAPPINGS) == ("linear", 0x8EDD, 0)
    gba = consoles.GBA
    assert _mapping_for(0x08001234, 0x1234, 4, gba, MAPPINGS) == ("gba", 0, 0)
    assert _mapping_for(0x1234, 0x1234, 2, gba, MAPPINGS)[0] == "linear"


def test_a_table_between_two_strings_is_extended():
    rom = rom_of()
    targets = [0x100, 0x300, 0x120, 0x140]
    for n, t in enumerate(targets):
        rom[0x200 + 2 * n : 0x202 + 2 * n] = t.to_bytes(2, "little")
    ss = [
        sight("a", result(0x100, 0x10F, pointers=slot(rom, 0x200, 0x100))),
        sight("b", result(0x300, 0x30F, pointers=slot(rom, 0x202, 0x300))),
    ]
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert (e.table.start, e.table.stop, e.table.confirmed) == (0x200, 0x208, True)


def test_one_capture_s_two_slots_are_no_stride():
    rom = rom_of()
    pointers = slot(rom, 0x200, 0x100) + slot(rom, 0x210, 0x100)
    r = result(0x100, 0x10F, pointers=pointers)
    (e,) = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS).engines
    assert not e.table.confirmed and e.table.slots == [0x200]


def test_nulls_and_shared_strings_between_the_slots_seen():
    rom = rom_of()
    values = [0x100, 0x0000, 0x100, 0x120, 0x140]
    for n, v in enumerate(values):
        rom[0x200 + 2 * n : 0x202 + 2 * n] = v.to_bytes(2, "little")
    ss = [
        sight("a", result(0x100, 0x10F, pointers=slot(rom, 0x200, 0x100))),
        sight("b", result(0x120, 0x12F, pointers=slot(rom, 0x206, 0x120))),
        sight("c", result(0x140, 0x14F, pointers=slot(rom, 0x208, 0x140))),
    ]
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    t = e.table
    assert (t.start, t.stop, t.stride) == (0x200, 0x20A, 2)
    assert (t.nulls, t.shared, t.null) == ([0x202], [0x204], 0)


def test_a_null_past_the_slots_seen_ends_the_table():
    # Two tables, one after the other, the first ended by a null.
    rom = rom_of(0x4000)
    first = [0x1000, 0x1020, 0x1040, 0x1060]
    second = [0x1800, 0x1820, 0x1840, 0x1860, 0x1880]
    for term in (0x0000, 0xFFFF):
        for n, v in enumerate(first + [term] + second):
            rom[0x100 + 2 * n : 0x102 + 2 * n] = v.to_bytes(2, "little")
        ss = [
            sight("a", result(0x1000, 0x1005, pointers=slot(rom, 0x100, 0x1000))),
            sight("b", result(0x1020, 0x1025, pointers=slot(rom, 0x102, 0x1020))),
        ]
        (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
        assert (e.table.start, e.table.stop, e.table.null) == (0x100, 0x108, None)


def test_a_repeated_string_past_the_slots_seen_ends_the_table():
    rom = rom_of()
    for n, v in enumerate([0x100, 0x120, 0x120, 0x140]):
        rom[0x200 + 2 * n : 0x202 + 2 * n] = v.to_bytes(2, "little")
    ss = [
        sight("a", result(0x100, 0x10F, pointers=slot(rom, 0x200, 0x100))),
        sight("b", result(0x120, 0x12F, pointers=slot(rom, 0x202, 0x120))),
    ]
    (e,) = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.table.stop == 0x204 and not e.table.shared


def test_a_split_pointer_is_reported_not_read():
    rom = rom_of()
    r = result(
        0x400, 0x405, pointers=[Pointer(0x300, 2, READ), Pointer(0x480, 512, READ)]
    )
    (e,) = combine([sight("a", r)], bytes(rom), CONSOLE, MAPPINGS).engines
    assert e.table is None and e.splits == [(0x300, 0x480)]


def test_a_tie_between_tables_is_settled_the_same_every_time():
    # Two readings, one capture and one sure slot each: the lowest slot wins,
    # whichever capture came first.
    rom = rom_of()
    a = sight("a", result(0x400, 0x405, pointers=slot(rom, 0x300, 0x400)))
    b = sight("b", result(0x420, 0x425, pointers=slot(rom, 0x200, 0x410)))
    got = [
        combine(order, bytes(rom), CONSOLE, MAPPINGS).engines[0].table
        for order in ([a, b], [b, a])
    ]
    assert [(t.slots, t.offset) for t in got] == [([0x200], 0x10)] * 2


# -- engines


def test_engines_are_the_routines_that_produced_the_text():
    rom = rom_of()
    ss = [
        sight("a", result(0x100, 0x105, reader=0x9000)),
        sight("b", result(0x200, 0x205, reader=0x9100)),
        sight("c", result(0x300, 0x305, reader=0x9000, unit=2)),
    ]
    engines = combine(ss, bytes(rom), CONSOLE, MAPPINGS).engines
    assert [(e.reader, sorted(e.starts)) for e in engines] == [
        (0x9000, ["a", "c"]),
        (0x9100, ["b"]),
    ]
    assert any("different widths" in n for n in engines[0].notes)
    assert not engines[0].writer


def test_a_dictionary_s_bytes_bound_no_string():
    r = result(
        0x100, 0x500, sources=[Source(0, 0x100, "copied"), Source(1, 0x180, "copied")]
    )
    d = Source(2, 0x500, "copied")
    d.stream = False  # as the tracer marks a dictionary's source
    r.sources.append(d)
    assert _bounds(r) == (0x100, 0x180)
    assert _bounds(replace(r, sources=r.sources[:2])) == (0x100, 0x500)


def test_an_sa1_short_pointer_below_8000_reads_as_hirom():
    # Read through a $Cx data bank: $E4:1234 is image $241234.
    rom = rom_of(0x400000, 0x00)
    ss = []
    for n, t in enumerate((0x241234, 0x241240)):
        r = result(t, t + 5, pointers=slot(rom, 0x200 + 2 * n, t & 0xFFFF))
        ss.append(sight("ab"[n], r))
    t = combine(ss, bytes(rom), consoles.SNES_SA1, MAPPINGS).engines[0].table
    assert (t.mapping_id, t.offset, t.bank) == ("hirom", 0, 0x24)
