"""What combined captures propose (:mod:`mapchar.capture.proposals`): entries
a new table takes whole, blocks read at the payload's offsets, ids that follow
what is proposed, and what is said to be unconfirmed."""

from __future__ import annotations

from capture_fake import CONSOLE
from helpers import ASCII_TABLE, table_set, texts
from mapchar.capture import consoles
from mapchar.capture.combine import Meaning, Sighting, combine
from mapchar.capture.proposals import (
    BLOCK,
    CONFLICT,
    ENTRIES,
    entry_for,
    propose,
    relabelled,
)
from mapchar.capture.trace import Code, Pointer, Result
from mapchar.core.block import FixedLength, NextPointer, PointerTableSource, RangeSource
from mapchar.core.table import Table, TableEntry, TokenKind
from mapchar.pipeline.extract import extract
from mapchar.plugins.registry import default_registry, resolve_mapping

REG = default_registry()
MAPPINGS = {i: resolve_mapping(REG, i) for i in ("linear", "lorom", "gba")}
READ = "read before"


def result(start, end, *, output="vram", reader=0x9000, **kw) -> Result:
    return Result(output=output, reader=reader, string=(start, end), **kw)


def slot(rom, at, value, size=2):
    rom[at : at + size] = value.to_bytes(size, "little")
    return [Pointer(at, 2, READ), Pointer(at + 1, 512, READ)]


def blocks(props):
    return [p for p in props if p.kind == BLOCK]


def test_every_end_code_gets_a_label_of_its_own():
    rom = bytearray(0x1000)
    rom[0x105], rom[0x205] = 0x00, 0xFF
    ss = [
        Sighting("a", "", result(0x100, 0x105, codes=[Code(0x00, "end")])),
        Sighting("b", "", result(0x200, 0x205, codes=[Code(0xFF, "end")])),
    ]
    c = combine(ss, bytes(rom), CONSOLE, MAPPINGS)
    (entries,) = [p for p in propose(c, "t") if p.kind == ENTRIES]
    ends = [(e.bits, e.text) for e in entries.entries if e.kind is TokenKind.END]
    assert ends == [("00000000", "[end]"), ("11111111", "[end-FF]")]
    table = Table("captured")
    for e in entries.entries:
        table.add(e)  # a new table takes them all
    assert set(table.labels) == {"end", "end-FF"}


def lorom_game(header: int) -> tuple[bytes, list[Sighting]]:
    """A LoROM image behind ``header`` bytes: a table at $200 of pointers
    into bank 1, reaching zero-ended ASCII strings."""
    image = bytearray(0x10000)
    ss = []
    at = 0x8000
    for n, word in enumerate([b"HELLO", b"WORLD", b"AGAIN"]):
        image[at : at + len(word) + 1] = word + b"\x00"
        pointers = slot(image, 0x200 + 2 * n, 0x8000 | (at & 0x7FFF))
        r = result(at, at + len(word), codes=[Code(0x00, "end")], pointers=pointers)
        if n < 2:
            ss.append(Sighting("abc"[n], "", r))
        at += len(word) + 1
    image[0x206:0x208] = b"\xee\xee"
    return bytes(header) + bytes(image), ss


def test_a_mapped_table_is_read_where_the_image_starts():
    data, ss = lorom_game(512)
    c = combine(ss, data[512:], consoles.SNES_LOROM, MAPPINGS)
    (block,) = blocks(propose(c, "main", shift=512))
    src = block.config.source
    assert isinstance(src, PointerTableSource)
    assert (src.start, src.stop, src.mapping_id, src.offset, src.bank) == (
        0x200 + 512,
        0x206 + 512,
        "lorom",
        512,
        1,
    )
    got = extract(data, block.config, table_set(ASCII_TABLE))
    assert texts(got) == ["HELLO[end]", "WORLD[end]", "AGAIN[end]"]


def test_slots_beyond_those_seen_are_counted():
    rom = bytearray([0xEE] * 0x1000)
    ss = []
    for n in range(3):
        target = 0x400 + 0x10 * n
        pointers = slot(rom, 0x200 + 4 * n, target)
        ss.append(Sighting("abc"[n], "", result(target, target + 5, pointers=pointers)))
    rom[0x20C:0x20E] = (0x430).to_bytes(2, "little")
    c = combine(ss, bytes(rom), CONSOLE, MAPPINGS)
    t = c.engines[0].table
    assert (t.start, t.stop, t.stride) == (0x200, 0x20E, 4)
    (block,) = blocks(propose(c, "t"))
    assert any(u.startswith("1 slots beyond those seen") for u in block.unconfirmed)
    assert isinstance(block.config.string_type, NextPointer)


def test_a_null_slot_is_the_source_s_null():
    rom = bytearray([0xEE] * 0x1000)
    for n, v in enumerate([0x100, 0x0000, 0x120, 0x140, 0x160]):
        rom[0x200 + 2 * n : 0x202 + 2 * n] = v.to_bytes(2, "little")
    ss = [
        Sighting("a", "", result(0x100, 0x10F, pointers=slot(rom, 0x200, 0x100))),
        Sighting("b", "", result(0x120, 0x12F, pointers=slot(rom, 0x204, 0x120))),
        Sighting("c", "", result(0x140, 0x14F, pointers=slot(rom, 0x206, 0x140))),
    ]
    (block,) = blocks(propose(combine(ss, bytes(rom), CONSOLE, MAPPINGS), "t"))
    assert block.config.source.null == 0
    assert "1 slots between those seen hold $0, read as no string" in block.unconfirmed
    assert any(u.startswith("1 slots beyond those seen") for u in block.unconfirmed)


def test_a_fixed_length_block_reaches_its_last_record():
    rom = bytearray(0x2000)
    ss = [
        Sighting(c, "", result(0x1000 + 0x10 * n, 0x1000 + 0x10 * n + 5))
        for n, c in enumerate("abd")
    ]
    ss[2].result.string = (0x1030, 0x1035)
    (block,) = blocks(propose(combine(ss, bytes(rom), CONSOLE, MAPPINGS), "t", 16))
    assert block.config.source == RangeSource(0x1000 + 16, 0x1040 + 16)
    assert block.config.string_type == FixedLength(16)
    assert any("fixed length of 16" in u for u in block.unconfirmed)


def test_a_split_pointer_is_unconfirmed():
    rom = bytearray(0x1000)
    r = result(
        0x400, 0x405, pointers=[Pointer(0x300, 2, READ), Pointer(0x480, 512, READ)]
    )
    (block,) = blocks(
        propose(combine([Sighting("a", "", r)], bytes(rom), CONSOLE, MAPPINGS), "t", 2)
    )
    assert any(
        "low byte at $302 and its high byte at $482" in u for u in block.unconfirmed
    )


def test_ids_follow_what_is_proposed():
    rom = bytearray([0xEE] * 0x1000)
    a = Sighting("a", "", result(0x100, 0x105, codes=[Code(0xFC, "command", skip=1)]))
    b = Sighting("b", "", result(0x200, 0x205, reader=0x9100))
    c = Sighting("c", "", result(0x300, 0x305, codes=[Code(0xFD, "command", skip=2)]))

    def ids(ss):
        return {
            p.id: p for p in propose(combine(ss, bytes(rom), CONSOLE, MAPPINGS), "t")
        }

    both = ids([a, b])
    swapped = ids([b, a])
    assert set(both) == set(swapped)
    only_b = ids([b])
    (bid,) = [i for i in only_b if i.startswith("block:")]
    assert bid in both and bid.startswith("block:9100:vram:")
    # Another capture of the same engine changes that engine's block and the
    # entries, not the other engine's block.
    more = ids([a, b, c])
    assert bid in more
    # A capture that extends a block keeps its id: it is known by where it
    # starts.
    (aid,) = [i for i in ids([a]) if i.startswith("block:9000")]
    assert aid in more
    assert [i for i in both if i.startswith("entries:")] != [
        i for i in more if i.startswith("entries:")
    ]


def test_a_block_says_which_routine_writes_or_reads_it():
    rom = bytearray(0x1000)
    r = result(0x100, 0x105, output="ram", reader=0x9010)
    (block,) = blocks(
        propose(combine([Sighting("a", "", r)], bytes(rom), CONSOLE, MAPPINGS), "t")
    )
    assert block.detail.startswith("writer 9010")


def test_conflicts_carry_each_reading_s_meaning():
    rom = bytearray(0x1000)
    ss = [
        Sighting("a", "", result(0x100, 0x105, codes=[Code(0xFC, "command", skip=1)])),
        Sighting("b", "", result(0x200, 0x205, codes=[Code(0xFC, "command", skip=2)])),
    ]
    (p,) = [
        p
        for p in propose(combine(ss, bytes(rom), CONSOLE, MAPPINGS), "t")
        if p.kind == CONFLICT
    ]
    entries = {r: entry_for(m) for r, m in p.conflict.choices.items()}
    assert sorted(e.operands[0].spec() for e in entries.values()) == ["u16", "u8"]


def test_entries_for_each_kind_of_meaning():
    def m(kind, **kw):
        return Meaning(0xFC, 1, kind, **kw)

    assert entry_for(m("text", text="a")) == TableEntry("11111100", TokenKind.TEXT, "a")
    assert entry_for(m("end")).text == "[end]"
    assert entry_for(m("command")).text == "[cFC]"
    three = entry_for(m("command", params=3))
    assert three.kind is TokenKind.CODE and three.operands[0].spec() == "3"
    # A reader's jump is no parameter count.
    assert entry_for(m("command", params=70000)).text == "[cFC]"
    assert entry_for(m("glyph")) is None
    assert entry_for(m("glyph"), "ā").text == "ā"


def test_relabelled_frees_labels_the_table_gives_other_bits():
    table = Table("t")
    table.add(TableEntry("00000000", TokenKind.END, "[end]"))
    table.add(TableEntry("00000001", TokenKind.CODE, "cFC"))
    entries = [
        TableEntry("00000000", TokenKind.END, "[end]"),
        TableEntry("11111111", TokenKind.END, "[end]"),
        TableEntry("11111100", TokenKind.CODE, "cFC"),
        TableEntry("11111101", TokenKind.TEXT, "x"),
    ]
    got = relabelled(entries, table)
    assert [e.text for e in got] == ["[end]", "[end2]", "cFC2", "x"]
