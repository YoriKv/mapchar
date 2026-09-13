"""The container plugins: what each one unwraps, what it puts back, what it
publishes on the context, and which files detection hands to it."""

from __future__ import annotations

import pytest

from mapchar.core.context import (
    KEY_HEADER_SIZE,
    KEY_SOURCE_OFFSET,
    KEY_SUGGESTED_MAPPING,
    PipelineContext,
)
from mapchar.core.notices import Level
from mapchar.pipeline.inspection import inspect_container
from mapchar.pipeline.pipeline import FileRef, PathwayConfig
from mapchar.plugins.base import ReadSource, Stage, WriteTarget
from mapchar.plugins.builtins.containers import (
    COPIER_HEADER,
    GB_GLOBAL_SUM_AT,
    GB_HEADER_SUM_AT,
    GB_LOGO,
    GBA_LOGO,
    KEY_N64_SWAP,
    N64_NATIVE,
    NES_MAGIC,
    ContainerField,
    GameBoy,
    INes,
    N64Rom,
    Smd,
    Snes,
    SnesHeadered,
    SnesInterleaved,
    n64_swap_width,
    repair_gb_checksums,
    swap_groups,
)


def ines_rom(*, prg: int = 1, chr_banks: int = 1, trainer: bool = False) -> bytes:
    flags = 0x04 if trainer else 0
    header = NES_MAGIC + bytes([prg, chr_banks, flags, 0]) + bytes(8)
    body = bytes((i * 7) & 0xFF for i in range(prg * 0x4000 + chr_banks * 0x2000))
    return header + (bytes(512) if trainer else b"") + body


def gb_rom() -> bytes:
    rom = bytearray(0x8000)
    rom[0x104 : 0x104 + len(GB_LOGO)] = GB_LOGO
    rom[0x134:0x143] = b"TESTROM".ljust(0x0F, b"\x00")
    return bytes(rom)


# -- iNES ------------------------------------------------------------------


def test_ines_reads_past_the_header_and_writes_behind_it() -> None:
    rom = ines_rom(prg=2, trainer=True)
    ctx = PipelineContext()
    body = INes().read(ReadSource(rom), ctx)
    assert body == rom[16 + 512 :]
    assert ctx.get(KEY_SOURCE_OFFSET) == ctx.get(KEY_HEADER_SIZE) == 16 + 512
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "banked"

    edited = bytes(b ^ 0xFF for b in body)
    out = INes().write(edited, WriteTarget(rom), ctx)
    assert out == rom[: 16 + 512] + edited


def test_ines_write_with_nothing_at_the_destination_rebuilds_the_cartridge() -> None:
    """A Save As copies the ROM, not the body: the body alone is not a
    cartridge and will not reopen as one."""
    rom = ines_rom(prg=2)
    ctx = PipelineContext()
    body = INes().read(ReadSource(rom), ctx)
    edited = bytes(b ^ 0xFF for b in body)

    out = INes().write(edited, WriteTarget(b""), ctx)
    assert out == rom[:16] + edited
    assert INes().read(ReadSource(out), PipelineContext()) == edited


def test_ines_without_the_magic_is_read_whole_and_says_so() -> None:
    plain = b"\x01\x02\x03\x04not-a-nes" + bytes(64)
    ctx = PipelineContext()
    assert INes().read(ReadSource(plain), ctx) == plain
    assert ctx.get(KEY_HEADER_SIZE) == 0
    assert ctx.get(KEY_SUGGESTED_MAPPING) is None
    assert [n.message for n in ctx.notices] == ["Not an iNES file: read as plain bytes"]
    # ...and the write half agrees: no header anywhere to preserve.
    assert INes().write(plain, WriteTarget(b""), ctx) == plain


def test_ines_describe_guards_a_file_shorter_than_its_header() -> None:
    fields = INes().describe(ReadSource(NES_MAGIC + b"\x01"), PipelineContext())
    assert [f.name for f in fields] == ["Header"]
    assert fields[0].value == "not an iNES image"


# -- SNES ------------------------------------------------------------------


def snes_rom(*, hirom: bool, size: int = 0x10000, checksum: bool = True) -> bytes:
    """A cartridge image with its internal header where that mapping puts it.

    ``checksum`` off leaves the complement pair zero, the way a hacked or
    truncated dump does, so the map mode byte is all there is to go on.
    """
    rom = bytearray(size)
    at = 0xFFC0 if hirom else 0x7FC0
    rom[at : at + 0x15] = b"TEST".ljust(0x15, b" ")
    rom[at + 0x15] = 0x21 if hirom else 0x20  # map mode: bit 0 is HiROM
    if checksum:
        rom[at + 0x1C : at + 0x1E] = (0x1234).to_bytes(2, "little")
        rom[at + 0x1E : at + 0x20] = (0x1234 ^ 0xFFFF).to_bytes(2, "little")
    return bytes(rom)


def test_snes_headered_suggests_the_mapping_its_header_states(registry) -> None:
    for hirom, expected in ((True, "hirom"), (False, "lorom")):
        rom = bytes(COPIER_HEADER) + snes_rom(hirom=hirom)
        ctx = PipelineContext()
        body = SnesHeadered().read(ReadSource(rom), ctx)
        assert body == rom[COPIER_HEADER:]
        assert ctx.get(KEY_SUGGESTED_MAPPING) == expected
        assert not ctx.notices  # the size rule agrees, so nothing to say
        assert SnesHeadered().write(body, WriteTarget(rom), ctx) == rom


def test_snes_headered_warns_when_the_size_rule_disagrees() -> None:
    """Picked by hand for a file that does not look headered, and the cost is
    512 real bytes off the front of the image."""
    rom = snes_rom(hirom=False)  # a whole number of KiB: no header
    ctx = PipelineContext()
    assert SnesHeadered().read(ReadSource(rom), ctx) == rom[COPIER_HEADER:]
    assert [n.message for n in ctx.notices] == ["This file does not look headered"]
    assert ctx.notices[0].level is Level.WARNING
    assert ctx.notices[0].source == "snes_headered"


def test_a_broken_checksum_still_leaves_the_map_mode_byte_to_decide() -> None:
    """The mode byte only counts where it is one. Padding reads as "bit clear",
    so crediting it would vote LoROM with a genuine byte's weight and a HiROM
    with a broken checksum pair would come back LoROM."""
    ctx = PipelineContext()
    Snes().read(ReadSource(snes_rom(hirom=True, checksum=False)), ctx)
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "hirom"
    ctx = PipelineContext()
    Snes().read(ReadSource(snes_rom(hirom=False, checksum=False)), ctx)
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "lorom"
    # Nothing that looks like a header anywhere: LoROM, the commoner layout.
    ctx = PipelineContext()
    Snes().read(ReadSource(bytes(0x10000)), ctx)
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "lorom"


def test_a_small_sfc_is_not_claimed_as_a_headered_dump(registry) -> None:
    # 1536 bytes is "512 over one KiB" like every headered dump, and nowhere
    # near a cartridge. Claiming it would hand back an empty document.
    assert registry.detect_container(bytes(1536), "tiles.sfc").info.id == "raw"
    assert registry.detect_container(bytes(COPIER_HEADER), "tiles.sfc").info.id == "raw"
    headered = bytes(COPIER_HEADER + 0x8000)
    assert registry.detect_container(headered, "g.sfc").info.id == "snes_headered"


def test_snes_interleave_restores_bank_order_both_ways() -> None:
    # Interleaved layout stores every bank's upper 32 KiB half first and then
    # all the lower halves, which is what puts the header at 0x7FC0.
    halves = [bytes([n]) * 0x8000 for n in range(4)]  # bank 0 = 0,1; bank 1 = 2,3
    interleaved = halves[1] + halves[3] + halves[0] + halves[2]

    ctx = PipelineContext()
    data = SnesInterleaved().read(ReadSource(interleaved), ctx)
    assert data == halves[0] + halves[1] + halves[2] + halves[3]
    assert ctx.get(KEY_SOURCE_OFFSET) == 0
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "hirom"
    assert SnesInterleaved().write(data, WriteTarget(interleaved), ctx) == interleaved


def test_snes_interleave_skips_a_copier_header_by_size() -> None:
    upper, lower = b"\x01" * 0x8000, b"\x00" * 0x8000
    rom = bytes(COPIER_HEADER) + upper + lower

    ctx = PipelineContext()
    assert SnesInterleaved().read(ReadSource(rom), ctx) == lower + upper
    assert ctx.get(KEY_SOURCE_OFFSET) == ctx.get(KEY_HEADER_SIZE) == COPIER_HEADER
    assert SnesInterleaved().write(lower + upper, WriteTarget(rom), ctx) == rom


def test_snes_interleave_keeps_a_partial_bank_and_says_so() -> None:
    rom = b"\x01" * 0x8000 + b"\x00" * 0x8000 + b"\xee" * 0x10
    ctx = PipelineContext()
    data = SnesInterleaved().read(ReadSource(rom), ctx)
    assert len(data) == 0x10000
    assert "Dropped 16 trailing byte(s)" in ctx.notices[0].message
    assert SnesInterleaved().write(data, WriteTarget(rom), ctx) == rom

    ctx = PipelineContext()
    assert SnesInterleaved().read(ReadSource(bytes(0x400)), ctx) == b""
    assert any("No complete 64 KiB bank" in n.message for n in ctx.notices)


def test_snes_interleave_is_never_detected(registry) -> None:
    # Nothing marks an interleaved image and deinterleaving a plain one
    # scrambles it, so this container is only ever chosen by hand.
    for name in ("g.sfc", "g.smc", "g.bin"):
        rom = bytes(COPIER_HEADER + 0x10000)
        assert registry.detect_container(rom, name).info.id != "snes_interleaved"
    assert registry.plugin(Stage.CONTAINER, "snes_interleaved") is not None


# -- Game Boy --------------------------------------------------------------


def test_gb_write_repairs_both_checksums() -> None:
    """The header sum is what the boot ROM checks — a wrong one is a blank
    screen on hardware — and the global sum is what a text edit invalidates."""
    rom = bytearray(gb_rom())
    ctx = PipelineContext()
    payload = GameBoy().read(ReadSource(bytes(rom)), ctx)
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "gb"

    edited = bytearray(payload)
    edited[0x4000] ^= 0xFF  # one edited byte, far from the header
    out = GameBoy().write(bytes(edited), WriteTarget(bytes(rom)), ctx)

    expected = 0
    for byte in out[0x134:GB_HEADER_SUM_AT]:
        expected = (expected - byte - 1) & 0xFF
    assert out[GB_HEADER_SUM_AT] == expected
    zeroed = bytearray(out)
    zeroed[GB_GLOBAL_SUM_AT : GB_GLOBAL_SUM_AT + 2] = b"\x00\x00"
    assert out[GB_GLOBAL_SUM_AT : GB_GLOBAL_SUM_AT + 2] == (
        sum(zeroed) & 0xFFFF
    ).to_bytes(2, "big")
    # Only the checksums and the edit differ; nothing else was rewritten.
    assert out[0x4000] == edited[0x4000]
    assert out[:GB_HEADER_SUM_AT] == bytes(rom[:GB_HEADER_SUM_AT])
    # Repairing an already repaired file changes nothing.
    assert repair_gb_checksums(out) == out


def test_gb_leaves_a_file_too_short_for_a_header_alone() -> None:
    short = b"\xaa" * 64
    assert repair_gb_checksums(short) == short
    assert GameBoy().write(short, WriteTarget(b""), PipelineContext()) == short
    fields = GameBoy().describe(ReadSource(short), PipelineContext())
    assert ("Checksums", "no header to repair") == fields[-1][:2]


def test_gb_describe_names_a_stale_checksum() -> None:
    rom = bytearray(gb_rom())
    rom[GB_HEADER_SUM_AT] = 0x42  # not what the fields add up to
    fields = {
        f.name: f.value
        for f in GameBoy().describe(ReadSource(bytes(rom)), PipelineContext())
    }
    assert (
        "in file" in fields["Header checksum"]
        and "correct" in fields["Header checksum"]
    )
    assert fields["Boot logo"] == "matches"


# -- Nintendo 64 -----------------------------------------------------------


def n64_rom() -> bytes:
    return N64_NATIVE + bytes((i * 11) & 0xFF for i in range(0x400 - 4))


@pytest.mark.parametrize("width", (0, 2, 4))
def test_n64_normalises_on_read_and_restores_the_order_on_write(width) -> None:
    native = n64_rom()
    on_disk = swap_groups(native, width)

    ctx = PipelineContext()
    assert N64Rom().read(ReadSource(on_disk), ctx) == native
    assert ctx.get(KEY_N64_SWAP) == width
    # Nothing is stripped, so no header size is published and none is claimed.
    assert ctx.get(KEY_HEADER_SIZE) is None and N64Rom().header_size() == 0
    assert not ctx.notices

    edited = bytearray(native)
    edited[0x11:0x13] = b"\xee\xff"  # an edit that is not group-aligned
    out = N64Rom().write(bytes(edited), WriteTarget(on_disk), ctx)
    assert out == swap_groups(bytes(edited), width)
    assert N64Rom().read(ReadSource(out), PipelineContext()) == bytes(edited)


def test_n64_write_without_a_read_takes_the_order_from_the_destination() -> None:
    native = n64_rom()
    on_disk = swap_groups(native, 2)
    out = N64Rom().write(native, WriteTarget(on_disk), PipelineContext())
    assert out == on_disk


def test_n64_swap_leaves_a_trailing_partial_group() -> None:
    assert swap_groups(b"\x01\x02\x03\x04\x05", 4) == b"\x04\x03\x02\x01\x05"
    assert swap_groups(b"\x01\x02\x03", 2) == b"\x02\x01\x03"
    assert swap_groups(b"\x01\x02\x03", 0) == b"\x01\x02\x03"


def test_n64_unrecognised_header_is_read_native_with_a_warning() -> None:
    junk = bytes(0x40)
    ctx = PipelineContext()
    assert N64Rom().read(ReadSource(junk), ctx) == junk
    assert n64_swap_width(junk) == 0
    assert [n.message for n in ctx.notices] == [
        "Unrecognised N64 header: assuming native byte order"
    ]


# -- Mega Drive ------------------------------------------------------------


def test_smd_deinterleaves_every_block_on_its_own() -> None:
    blocks = bytes((i * 5 + 1) & 0xFF for i in range(2 * 16384))
    body = b""
    for at in (0, 16384):
        block = blocks[at : at + 16384]
        body += block[1::2] + block[0::2]  # odd bytes first, then even
    rom = bytes(512) + body

    ctx = PipelineContext()
    data = Smd().read(ReadSource(rom), ctx)
    assert data == blocks
    assert ctx.get(KEY_SOURCE_OFFSET) == ctx.get(KEY_HEADER_SIZE) == 512
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "linear"
    assert Smd().write(data, WriteTarget(rom), ctx) == rom


def test_smd_keeps_a_partial_block_and_says_so() -> None:
    rom = bytes(512) + bytes(16384) + b"\xee" * 8
    ctx = PipelineContext()
    data = Smd().read(ReadSource(rom), ctx)
    assert len(data) == 16384
    assert "Dropped 8 trailing byte(s)" in ctx.notices[0].message
    assert Smd().write(data, WriteTarget(rom), ctx) == rom

    ctx = PipelineContext()
    assert Smd().read(ReadSource(bytes(512)), ctx) == b""
    assert any("No complete 16 KiB block" in n.message for n in ctx.notices)


# -- detection and the shared hooks ----------------------------------------


DETECTION = {
    # name: (file name, leading bytes, the container that claims it)
    "nes by magic": (
        "game.nes",
        NES_MAGIC + bytes([1, 1, 0, 0]) + bytes(0x100),
        "ines",
    ),
    "nes misnamed": (
        "game.bin",
        NES_MAGIC + bytes([1, 1, 0, 0]) + bytes(0x100),
        "ines",
    ),
    "nes suffix alone": ("game.nes", b"not-an-ines" + bytes(0x100), "raw"),
    "gb by logo": ("game.gb", bytes(0x104) + GB_LOGO + bytes(0x100), "gb"),
    "gb misnamed": ("game.bin", bytes(0x104) + GB_LOGO + bytes(0x100), "gb"),
    "gba by logo": ("game.gba", bytes(4) + GBA_LOGO + bytes(0x100), "gba"),
    "n64 native": ("game.z64", N64_NATIVE + bytes(0x400), "n64"),
    "n64 byteswapped": ("game.v64", b"\x37\x80\x40\x12" + bytes(0x400), "n64"),
    "n64 little-endian": ("game.bin", b"\x40\x12\x37\x80" + bytes(0x400), "n64"),
    "n64 suffix alone": ("game.z64", bytes(0x400), "raw"),
    "smd by suffix": ("game.smd", bytes(512 + 16384), "smd"),
    "snes headerless": ("game.sfc", bytes(0x8000), "snes"),
    "snes headered": ("game.smc", bytes(512 + 0x8000), "snes_headered"),
    "rom-sized binary": ("game.bin", bytes(0x8000), "raw"),
    "small binary": ("game.bin", bytes(100), "raw"),
}


@pytest.mark.parametrize("case", DETECTION.values(), ids=DETECTION.keys())
def test_detection_claims_the_same_files_it_can_unwrap(registry, case) -> None:
    """A signature claims a file whatever it is named, a suffix alone never
    overrules one, and a size rule claims nothing on its own."""
    name, data, expected = case
    assert registry.detect_container(data, name).info.id == expected


def test_a_slot_write_puts_the_bytes_back_where_they_came_from(registry) -> None:
    """A block's compressed slot inside its parent is not the whole file, so no
    container unwraps framing around it: the write lands in the slot and every
    byte outside it survives."""
    file_bytes = bytes(range(256))  # too short to hold any format's header
    edit = b"\xaa\xbb\xcc\xdd"
    for plugin in registry.plugins(Stage.CONTAINER):
        target = WriteTarget(file_bytes, (), 0x40, len(edit))
        out = plugin.write(edit, target, PipelineContext())
        assert out[0x40:0x44] == edit, plugin.info.id
        assert out[:0x40] == file_bytes[:0x40], plugin.info.id
        assert out[0x44:] == file_bytes[0x44:], plugin.info.id

    # The Game Boy is the one that still acts outside the slot, because the
    # checksums are its to keep true whatever the write was.
    rom = gb_rom()
    out = GameBoy().write(
        edit, WriteTarget(rom, (), 0x4000, len(edit)), PipelineContext()
    )
    assert out[0x4000:0x4004] == edit
    assert out == repair_gb_checksums(out)
    assert out[:GB_HEADER_SUM_AT] == rom[:GB_HEADER_SUM_AT]


def test_an_n64_slot_write_splices_in_native_order() -> None:
    """The one container whose slot write is not a plain splice.

    An offset that is not group-aligned names different bytes on disk than in
    native order, and native is what every published offset is quoted in — so the
    splice happens there and the file is swapped back around it.
    """
    native = n64_rom()
    on_disk = swap_groups(native, 2)  # a .v64 dump
    ctx = PipelineContext()
    assert N64Rom().read(ReadSource(on_disk), ctx) == native
    edit = b"\xaa\xbb"
    out = N64Rom().write(edit, WriteTarget(on_disk, (), 0x41, len(edit)), ctx)
    assert out[:4] == on_disk[:4]  # still a .v64 dump, order untouched
    assert N64Rom().read(ReadSource(out), PipelineContext()) == (
        native[:0x41] + edit + native[0x43:]
    )


def test_every_container_answers_the_optional_hooks(registry) -> None:
    """``header_size``, ``default_mapping`` and ``describe`` are the container
    hooks the block dialog and Container Info read, so every built-in has
    them — and none of them raises on a file too short to hold a header."""
    for plugin in registry.plugins(Stage.CONTAINER):
        assert isinstance(plugin.header_size(), int)
        assert plugin.default_mapping() in (
            None,
            "gb",
            "gba",
            "banked",
            "hirom",
            "linear",
        )
        for data in (b"", b"\x00" * 3, bytes(0x400)):
            fields = plugin.describe(ReadSource(data), PipelineContext())
            assert fields and all(isinstance(f, ContainerField) for f in fields)
            assert all(f.name and f.value and f.detail for f in fields)


def test_a_flat_read_does_not_overwrite_an_inherited_header_size(registry) -> None:
    """A compressed block reads a slot of its parent through the flat container,
    on the parent's context. Publishing a zero header size there would tell the
    block's pointer mappings to subtract nothing from every value."""
    parent = PipelineContext()
    registry.plugin(Stage.CONTAINER, "ines").read(ReadSource(ines_rom()), parent)
    assert parent.get(KEY_HEADER_SIZE) == 16

    block = parent.inherit()
    registry.plugin(Stage.CONTAINER, "raw").read(ReadSource(bytes(8)), block)
    assert block.get(KEY_HEADER_SIZE) == 16


def test_container_info_shows_the_fields_a_container_describes(tmp_path, registry):
    """The report behind Container Info reads the describe hook, detail and all:
    the value says what the file holds and the detail what was done with it."""
    rom = tmp_path / "g.nes"
    rom.write_bytes(ines_rom())
    report = inspect_container(PathwayConfig(FileRef((str(rom),)), "ines"), registry)
    assert not report.error
    rows = {f.name: f for f in report.fields}
    assert rows["Mapper"].value == "0" and rows["Mapper"].detail
    assert rows["Header"].value == "iNES, 16 bytes"


def test_a_suggested_mapping_is_published_by_every_container_that_knows_one(
    registry,
) -> None:
    expected = {
        "ines": "banked",
        "gb": "gb",
        "gba": "gba",
        "snes": "lorom",
        "snes_headered": "lorom",
        "snes_interleaved": "hirom",
        "smd": "linear",
    }
    for id, mapping in expected.items():
        plugin = registry.plugin(Stage.CONTAINER, id)
        ctx = PipelineContext()
        rom = ines_rom() if id == "ines" else bytes(512 + 0x10000)
        plugin.read(ReadSource(rom), ctx)
        assert ctx.get(KEY_SUGGESTED_MAPPING) == mapping, id
        assert registry.plugin(Stage.MAPPING, mapping) is not None
    # The two that cannot know keep quiet rather than guessing.
    for id in ("raw", "n64"):
        ctx = PipelineContext()
        registry.plugin(Stage.CONTAINER, id).read(ReadSource(n64_rom()), ctx)
        assert ctx.get(KEY_SUGGESTED_MAPPING) is None
