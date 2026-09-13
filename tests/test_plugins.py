from __future__ import annotations

from mapchar.core.context import KEY_HEADER_SIZE, KEY_SUGGESTED_MAPPING, PipelineContext
from mapchar.core.table import Table
from mapchar.plugins.base import ReadSource, Stage, WriteTarget, writes_back
from mapchar.plugins.builtins.containers import GB_LOGO, GBA_LOGO, NES_MAGIC
from mapchar.plugins.charsets import apply_charset
from mapchar.plugins.registry import PassThrough


def test_detection_and_roundtrip(registry):
    nes = NES_MAGIC + bytes([1, 1, 0, 0]) + b"\x00" * 8 + b"\xaa" * 32
    plugin = registry.detect_container(nes, "game.nes")
    assert plugin.info.id == "ines"
    ctx = PipelineContext()
    payload = plugin.read(ReadSource(nes), ctx)
    assert payload == b"\xaa" * 32 and ctx.get(KEY_HEADER_SIZE) == 16
    assert plugin.write(payload, WriteTarget(nes), ctx) == nes
    assert plugin.describe(ReadSource(nes), ctx)["Mapper"] == 0

    gb = b"\x00" * 0x104 + GB_LOGO + b"\x00" * 0x100
    assert registry.detect_container(gb, "x.bin").info.id == "gb"
    gba = b"\x00" * 4 + GBA_LOGO + b"\x00" * 0x100
    assert registry.detect_container(gba).info.id == "gba"
    smc = b"\x00" * 512 + b"\x00" * 0x8000
    assert registry.detect_container(smc, "x.smc").info.id == "snes_headered"
    assert registry.detect_container(b"\x00" * 0x8000, "x.sfc").info.id == "snes"
    assert registry.detect_container(b"\x00" * 100, "x.bin").info.id == "raw"


def test_snes_mapping_guess(registry):
    rom = bytearray(0x10000)
    rom[0xFFDC:0xFFDE] = (0x1234).to_bytes(2, "little")
    rom[0xFFDE:0xFFE0] = (0x1234 ^ 0xFFFF).to_bytes(2, "little")
    ctx = PipelineContext()
    registry.plugin(Stage.CONTAINER, "snes").read(ReadSource(bytes(rom)), ctx)
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "hirom"
    ctx = PipelineContext()
    registry.plugin(Stage.CONTAINER, "snes").read(ReadSource(bytes(0x10000)), ctx)
    assert ctx.get(KEY_SUGGESTED_MAPPING) == "lorom"


def test_missing_plugin_degrades(registry):
    p = registry.resolve_stage(Stage.COMPRESSION, "nope")
    assert isinstance(p, PassThrough) and not writes_back(p, Stage.COMPRESSION)
    assert registry.resolve_stage(Stage.COMPRESSION, None) is None


def test_charsets(registry):
    t = Table("t", "ascii")
    from mapchar.core.table import Entry, EntryKind

    t.add(Entry("01000001", EntryKind.TEXT, "a"))  # override A
    t.add(Entry("01000010", EntryKind.TEXT, ""))  # remove B
    apply_charset(t, registry)
    assert t.entries["01000001"].text == "a"
    assert "01000010" not in t.entries
    assert t.entries["01000011"].text == "C"
    assert t.entries["01011011"].text == "\\["
    sj = Table("s", "shift-jis")
    apply_charset(sj, registry)
    assert sj.entries["1000001010100000"].text == "あ"
