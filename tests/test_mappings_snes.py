"""The SNES pointer mappings past plain LoROM and HiROM: ExHiROM's second
4 MiB and the SA-1's banks ``$80-$BF``. Each agrees with the capture profile's
own address math (:mod:`mapchar.capture.consoles`) over every bank, reads and
writes the pointers of a block, and says where no address reaches."""

from __future__ import annotations

from helpers import ASCII_TABLE, table_set, texts
from mapchar.capture import consoles
from mapchar.core.block import BlockConfig, EndToken, PointerTableSource
from mapchar.pipeline.extract import extract
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import default_registry, resolve_mapping

REG = default_registry()
EXHIROM = resolve_mapping(REG, "exhirom")
SA1 = resolve_mapping(REG, "sa1")
LOWS = (0x0000, 0x1234, 0x7FFF, 0x8000, 0xABCD, 0xFFFF)


def test_both_are_registered_with_their_sizes():
    ids = REG.ids(Stage.MAPPING)
    assert "exhirom" in ids and "sa1" in ids
    assert EXHIROM.sizes == SA1.sizes == (2, 3)
    assert EXHIROM.needs_bank and SA1.needs_bank


def test_exhirom_spells_each_half_of_the_image():
    cases = [
        (0x000000, 0xC00000),
        (0x12345, 0xC12345),
        (0x3FFFFF, 0xFFFFFF),
        (0x400000, 0x400000),
        (0x5ABCDE, 0x5ABCDE),
        (0x7DFFFF, 0x7DFFFF),
    ]
    for offset, value in cases:
        assert EXHIROM.to_value(offset) == value
        assert EXHIROM.to_offset(value) == offset
        # A 16-bit pointer takes its bank from the block.
        bank = EXHIROM.bank_of(offset)
        assert EXHIROM.to_offset(value & 0xFFFF, bank) == offset
    # The mirrors: $80-$BF and $00-$3D upper halves.
    assert EXHIROM.to_offset(0x81ABCD) == 0x01ABCD
    assert EXHIROM.to_offset(0x01ABCD) == 0x41ABCD
    assert EXHIROM.to_offset(0x811234) is None  # system area, not ROM
    assert EXHIROM.to_offset(0x7E1234) is None  # work RAM
    # The last 128 KiB: $7E-$7F are work RAM, $3E-$3F:8000+ reach its upper
    # halves, and nothing its lower.
    assert EXHIROM.to_offset(0x3E8000) == 0x7E8000
    assert EXHIROM.to_value(0x7FABCD) == 0x3FABCD
    assert EXHIROM.to_value(0x7E1234) == -1
    assert EXHIROM.to_value(0x800000) == -1


def test_sa1_spells_its_four_windows_lorom_style():
    cases = [
        (0x000000, 0x008000),
        (0x0FFFFF, 0x1FFFFF),
        (0x100000, 0x208000),
        (0x200000, 0x808000),
        (0x2ABCDE, 0x95BCDE),
        (0x300000, 0xA08000),
        (0x3FFFFF, 0xBFFFFF),
    ]
    for offset, value in cases:
        assert SA1.to_value(offset) == value
        assert SA1.to_offset(value) == offset
        assert SA1.to_offset(value & 0xFFFF, SA1.bank_of(offset)) == offset
    # $C0-$FF: the same four MiB, HiROM-style; $80-$BF no FastROM mirror.
    assert SA1.to_offset(0xE12345) == 0x212345
    assert SA1.to_offset(0x808000) != SA1.to_offset(0x008000)
    assert SA1.to_offset(0x001234) is None and SA1.to_offset(0x408000) is None
    assert SA1.to_value(0x400000) == -1


def test_they_agree_with_the_capture_profiles_over_every_bank():
    for mapping, con in ((EXHIROM, consoles.SNES_EXHIROM), (SA1, consoles.SNES_SA1)):
        # Bank $00 is left out: a value that fits 16 bits is a short pointer,
        # its bank the block's.
        for bank in range(1, 0x100):
            for low in LOWS:
                bus = bank << 16 | low
                off = mapping.to_offset(bus)
                if off is not None:
                    assert off == con.to_rom(bus), (con.id, hex(bus))
        for off in [*range(0, 0x800000, 0x1F37), 0x7E8000, 0x7FFFFF]:
            spellings = con.to_bus(off)
            for bus in spellings:
                if bus > 0xFFFF:
                    assert mapping.to_offset(bus) == off, (con.id, hex(bus))
            if spellings:
                assert mapping.to_value(off) in spellings, (con.id, hex(off))


def test_a_block_reads_through_either_mapping():
    ts = table_set(ASCII_TABLE)
    # ExHiROM: a table of 3-byte pointers into the second 4 MiB.
    image = bytearray(0x600000)
    targets = [0x512340, 0x512350]
    for n, (t, word) in enumerate(zip(targets, (b"UPPER", b"HALF"), strict=True)):
        image[t : t + len(word) + 1] = word + b"\x00"
        image[0x100 + 3 * n : 0x103 + 3 * n] = EXHIROM.to_value(t).to_bytes(3, "little")
    cfg = BlockConfig(
        PointerTableSource(0x100, 0x106, 3, 3, "little", "exhirom"), EndToken(), "main"
    )
    assert texts(extract(bytes(image), cfg, ts, REG)) == ["UPPER[end]", "HALF[end]"]
    # SA-1: 16-bit pointers into bank $80 (the image's third MiB).
    image = bytearray(0x300000)
    targets = [0x200010, 0x200020]
    for n, (t, word) in enumerate(zip(targets, (b"THIRD", b"MIB"), strict=True)):
        image[t : t + len(word) + 1] = word + b"\x00"
        image[0x100 + 2 * n : 0x102 + 2 * n] = (SA1.to_value(t) & 0xFFFF).to_bytes(
            2, "little"
        )
    bank = SA1.bank_of(0x200010)
    cfg = BlockConfig(
        PointerTableSource(0x100, 0x104, 2, 2, "little", "sa1", 0, bank),
        EndToken(),
        "main",
    )
    assert texts(extract(bytes(image), cfg, ts, REG)) == ["THIRD[end]", "MIB[end]"]


def test_pointer_discovery_finds_them():
    from mapchar.engines.pointers import discover

    image = bytearray(0x600000)
    starts = [0x512340, 0x512350, 0x512360]
    for n, t in enumerate(starts):
        image[0x100 + 3 * n : 0x103 + 3 * n] = EXHIROM.to_value(t).to_bytes(3, "little")
    best = discover(
        bytes(image), starts, {"exhirom": EXHIROM}, sizes=(3,), endians=("little",)
    )[0]
    assert best.mapping_id == "exhirom" and best.hits[0x512340] == [0x100]
    image = bytearray(0x300000)
    image[0x7FD5] = 0x23  # the header names the SA-1
    starts = [0x200010, 0x200020, 0x200030]
    for n, t in enumerate(starts):
        image[0x100 + 3 * n : 0x103 + 3 * n] = SA1.to_value(t).to_bytes(3, "little")
    best = discover(
        bytes(image), starts, {"sa1": SA1}, sizes=(3,), endians=("little",)
    )[0]
    assert best.mapping_id == "sa1" and best.hits[0x200020] == [0x103]


def test_discovery_tries_them_only_where_they_can_hold():
    from mapchar.engines.pointers import discover

    maps = {m: resolve_mapping(REG, m) for m in ("hirom", "lorom", "exhirom", "sa1")}
    # 4 MiB or less: ExHiROM reads as HiROM does, so it is not tried.
    assert not EXHIROM.applies(bytes(0x400000)) and EXHIROM.applies(bytes(0x400001))
    image = bytearray(0x300000)
    starts = [0x12340, 0x12350, 0x12360]
    for n, t in enumerate(starts):
        image[0x100 + 3 * n : 0x103 + 3 * n] = (0xC00000 + t).to_bytes(3, "little")
    found = {c.mapping_id for c in discover(bytes(image), starts, maps, sizes=(3,))}
    assert "hirom" in found and "exhirom" not in found and "sa1" not in found
    # A header naming the SA-1 lets it be tried; a random map mode does not.
    assert not SA1.applies(bytes(image))
    image[0x7FD5], image[0x7FD6] = 0x20, 0x35
    assert SA1.applies(bytes(image))
    image[0x7FD5], image[0x7FD6] = 0x33, 0x00
    assert SA1.applies(bytes(image))
    image[0x7FD5], image[0x7FD6] = 0x13, 0x35
    assert not SA1.applies(bytes(image))
    # A plugin without ``applies``, or one that raises, is still tried.

    class Plain:
        sizes = (3,)
        needs_bank = False

        def to_offset(self, value, bank=0, ptr_address=0):
            return value - 0xC00000 if value >= 0xC00000 else None

        def to_value(self, offset, bank=0, ptr_address=0):
            return 0xC00000 + offset

    class Raising(Plain):
        def applies(self, data):
            raise RuntimeError

    for plugin in (Plain(), Raising()):
        cands = discover(bytes(image), starts, {"p": plugin}, sizes=(3,))
        assert cands and cands[0].mapping_id == "p"
