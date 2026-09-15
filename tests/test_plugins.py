from __future__ import annotations

from mapchar.core.bits import bytes_to_bits
from mapchar.core.block import BlockConfig, PointerTableSource
from mapchar.core.context import KEY_HEADER_SIZE, KEY_SUGGESTED_MAPPING, PipelineContext
from mapchar.core.table import Table
from mapchar.plugins.aliases import RENAMED, current_config_ids, current_id
from mapchar.plugins.base import (
    PluginInfo,
    ReadSource,
    Stage,
    WriteTarget,
    writes_back,
)
from mapchar.plugins.builtins.containers import GB_LOGO, GBA_LOGO, NES_MAGIC
from mapchar.plugins.charsets import apply_charset
from mapchar.plugins.registry import PassThrough, resolve_mapping


def test_detection_and_roundtrip(registry):
    nes = NES_MAGIC + bytes([1, 1, 0, 0]) + b"\x00" * 8 + b"\xaa" * 32
    plugin = registry.detect_container(nes, "game.nes")
    assert plugin.info.id == "ines"
    ctx = PipelineContext()
    payload = plugin.read(ReadSource(nes), ctx)
    assert payload == b"\xaa" * 32 and ctx.get(KEY_HEADER_SIZE) == 16
    assert plugin.write(payload, WriteTarget(nes), ctx) == nes
    fields = {f.name: f.value for f in plugin.describe(ReadSource(nes), ctx)}
    assert fields["Mapper"] == "0"

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
    from mapchar.core.table import Entry, TokenKind

    t.add(Entry("01000001", TokenKind.TEXT, "a"))  # override A
    t.add(Entry("01000010", TokenKind.TEXT, ""))  # remove B
    apply_charset(t, registry)
    assert t.entries["01000001"].text == "a"
    assert "01000010" not in t.entries
    assert t.entries["01000011"].text == "C"
    assert t.entries["01011011"].text == "\\["
    sj = Table("s", "shift-jis")
    apply_charset(sj, registry)
    assert sj.entries["1000001010100000"].text == "あ"
    # cp932, so the NEC and IBM rows are there too, and the yen sign reaches
    # the byte JIS X 0201 gives it while ASCII still decodes it.
    assert sj.entries[bytes_to_bits("髙".encode("cp932"))].text == "髙"
    assert sj.entries["01011100"].text == "\\\\" and sj.aliases["¥"] == "01011100"
    utf8 = Table("u", "utf-8")
    apply_charset(utf8, registry)
    assert utf8.entries[bytes_to_bits("𩸽".encode())].text == "𩸽"


def test_a_size_rule_only_rejects(registry):
    """A container's size rules narrow what it claims and never claim anything.

    Any ROM is a multiple of 32 KiB, so scoring one would make every unnamed
    binary a SNES cartridge and every 512-past-a-kilobyte one a headered dump —
    a detection that says something about the file only by accident.
    """
    assert registry.detect_container(b"\x00" * 0x8000, "dump.bin").info.id == "raw"
    headered = b"\x00" * (512 + 0x8000)
    assert registry.detect_container(headered, "dump.bin").info.id == "raw"
    # The same bytes under a name the container declares: now it claims them.
    assert registry.detect_container(headered, "dump.smc").info.id == "snes_headered"


def test_any_magic_probe_detects(registry):
    """Several probes are alternatives, not a conjunction."""

    class TwoWays:
        info = PluginInfo(
            "two_ways",
            "Either signature",
            Stage.CONTAINER,
            magic=((0, b"AAAA"), (8, b"BBBB")),
        )

        def read(self, source, ctx):
            return source.data

    registry.register(TwoWays())
    assert registry.detect_container(b"AAAA" + bytes(16)).info.id == "two_ways"
    assert (
        registry.detect_container(bytes(8) + b"BBBB" + bytes(8)).info.id == "two_ways"
    )
    assert registry.detect_container(bytes(24)).info.id == "raw"


def test_detection_reads_a_head_not_the_file(registry):
    """Detection takes the leading bytes and the length separately, so opening a
    30 MiB ROM is not a 30 MiB comparison."""
    rom = bytes(0x100) + GB_LOGO.join((b"", b""))  # a file with no signature
    head = (b"\x00" * 0x104) + GB_LOGO + b"\x00" * 8
    assert registry.detect_container(head, "x.bin", 0x200000).info.id == "gb"
    assert registry.detect_container(rom, "x.bin", 0x200000).info.id == "raw"


def test_the_rename_table_stays_honest(registry):
    """Both invariants the alias table promises, checked against the registry.

    A rename is only half-done until this passes: an alias shadowed by a live
    plugin never fires, and one pointing at a name that no longer exists
    forwards a saved project to nothing.
    """
    live = {p.info.id for stage in Stage for p in registry.plugins(stage)}
    assert not (set(RENAMED) & live), sorted(set(RENAMED) & live)
    unresolved = {
        old: current_id(old) for old in RENAMED if current_id(old) not in live
    }
    assert not unresolved, unresolved
    # "Re-pointed" is the half a chain-following check cannot see: a target that
    # is itself retired resolves today only because current_id walks.
    midway = {old: new for old, new in RENAMED.items() if new in RENAMED}
    assert not midway, midway


def test_a_renamed_id_still_resolves(registry, monkeypatch):
    """The point of the table: an id saved before a rename still opens."""
    monkeypatch.setitem(RENAMED, "old_flat", "raw")
    assert registry.plugin(Stage.CONTAINER, "old_flat").info.id == "raw"
    assert not isinstance(
        registry.resolve_stage(Stage.CONTAINER, "old_flat"), PassThrough
    )
    # An id that was never renamed is not invented: a genuinely missing plugin
    # still degrades, which is what leaves the entry view-only.
    assert registry.plugin(Stage.CONTAINER, "never_existed") is None


def test_a_live_plugin_beats_a_retired_name(registry, monkeypatch):
    """A user is entitled to any id they like, including one mapchar retired.
    Theirs is the plugin in front of them, so it wins over the forwarding."""
    monkeypatch.setitem(RENAMED, "old_flat", "raw")

    class Squatter:
        info = PluginInfo("old_flat", "Someone's own", Stage.CONTAINER)

        def read(self, source, ctx):
            return source.data

    registry.register(Squatter())
    assert registry.plugin(Stage.CONTAINER, "old_flat").info.name == "Someone's own"
    assert registry.plugin(Stage.CONTAINER, "raw").info.name != "Someone's own"


def test_current_id_survives_a_cycle(monkeypatch):
    """The table is hand-edited. A cycle is a bug in it, but it must fail a test
    rather than hang the app on a lookup."""
    monkeypatch.setattr("mapchar.plugins.aliases.RENAMED", {"a": "b", "b": "a"})
    assert current_id("a") in ("a", "b")


def test_a_project_config_follows_a_renamed_mapping(monkeypatch):
    """A block's mapping is stored inside its config string, and gets the same
    forwarding the container and compression ids get."""
    monkeypatch.setitem(RENAMED, "old_banked", "banked")
    config = BlockConfig(PointerTableSource(0, 8, 2, 2, "little", "old_banked"))
    assert current_config_ids(config).source.mapping_id == "banked"


def test_a_parameterised_mapping_id_is_forwarded_by_its_head(registry, monkeypatch):
    """A banked mapping carries its numbers inside its id, so an exact lookup
    could never forward one: the head is what a row renames and the parameters
    ride along, which is one row for every parameterisation."""
    monkeypatch.setitem(RENAMED, "oldbanked", "banked")
    assert current_id("oldbanked:8000:4000") == "banked:8000:4000"
    # Through the door every mapping lookup goes through, including the
    # parameterised ids nothing registers.
    mapping = resolve_mapping(registry, "oldbanked:8000:4000")
    assert mapping is not None and mapping.info.id == "banked:8000:4000"
    assert mapping.to_value(0x100) == 0x8100
    # And through a project load, which rewrites what it stored.
    config = BlockConfig(
        PointerTableSource(0, 8, 2, 2, "little", "oldbanked:8000:4000")
    )
    assert current_config_ids(config).source.mapping_id == "banked:8000:4000"
    # An id with no head to rename is untouched, parameters and all.
    assert current_id("banked:C000:2000") == "banked:C000:2000"
