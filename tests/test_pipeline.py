from __future__ import annotations

import pytest

from mapchar.core.context import KEY_HEADER_SIZE, KEY_SOURCE_FILES, PipelineContext
from mapchar.core.errors import PipelineError
from mapchar.pipeline.inspection import inspect_container
from mapchar.pipeline.pipeline import (
    FileRef,
    PathwayConfig,
    SlotFill,
    compress_for_slot,
    find_next_structure,
    load,
    save,
)
from mapchar.plugins.base import PluginInfo, Stage, WriteTarget
from mapchar.plugins.builtins.containers import NES_MAGIC


def test_load_and_save_roundtrip(tmp_path, registry):
    header = NES_MAGIC + bytes([1, 0, 0, 0]) + b"\x00" * 8
    body = bytes(range(64))
    rom = tmp_path / "g.nes"
    rom.write_bytes(header + body)
    cfg = PathwayConfig(FileRef((str(rom),)), "ines")
    loaded = load(cfg, registry)
    assert loaded.writable and not loaded.missing_plugins
    assert loaded.ctx.get(KEY_HEADER_SIZE) == 16
    assert loaded.ctx.get(KEY_SOURCE_FILES) == ((str(rom), 0, len(header) + len(body)),)
    assert loaded.data == body
    new = bytearray(loaded.data)
    new[1] = 0xEE
    assert save(bytes(new), cfg, registry, loaded.ctx) == [str(rom)]
    out = rom.read_bytes()
    assert out[:16] == header and out[17] == 0xEE and out[16] == 0
    assert save(bytes(new), cfg, registry, loaded.ctx) == []


def test_save_reads_the_destination_as_it_stands(tmp_path, registry):
    """A write keeps a change made to the file since the entry opened.

    The container is handed the destination's bytes *now*, not the ones the load
    remembered, so the bytes it preserves around the payload are the real ones.
    """
    rom = tmp_path / "g.nes"
    header = NES_MAGIC + bytes([1, 0, 0, 0]) + b"\x00" * 8
    rom.write_bytes(header + b"\x00" * 16)
    cfg = PathwayConfig(FileRef((str(rom),)), "ines")
    loaded = load(cfg, registry)
    moved = bytearray(rom.read_bytes())
    moved[10] = 0x5A  # something else edited the header while the entry was open
    rom.write_bytes(bytes(moved))
    save(b"\xaa" * 16, cfg, registry, loaded.ctx)
    assert rom.read_bytes() == bytes(moved[:16]) + b"\xaa" * 16


def test_missing_plugin_is_view_only_and_says_so(tmp_path, registry):
    rom = tmp_path / "x.bin"
    rom.write_bytes(b"abc")
    loaded = load(PathwayConfig(FileRef((str(rom),)), "raw", "gone"), registry)
    assert loaded.data == b"abc" and not loaded.writable
    assert loaded.missing_plugins == ["gone"]
    assert [n.message for n in loaded.ctx.notices] == ["Missing plugin: gone"]


def test_missing_source_file_hard_stops(tmp_path, registry):
    cfg = PathwayConfig(FileRef((str(tmp_path / "nope.bin"),)), "raw")
    with pytest.raises(PipelineError) as caught:
        load(cfg, registry)
    assert caught.value.stage is Stage.CONTAINER and caught.value.action == "read"


def test_joined_files(tmp_path, registry):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"AA")
    b.write_bytes(b"BB")
    cfg = PathwayConfig(FileRef((str(a), str(b))), "raw")
    loaded = load(cfg, registry)
    assert loaded.data == b"AABB"
    assert loaded.ctx.get(KEY_SOURCE_FILES) == ((str(a), 0, 2), (str(b), 2, 2))
    assert save(b"AAXX", cfg, registry, loaded.ctx) == [str(b)]
    assert b.read_bytes() == b"XX" and a.read_bytes() == b"AA"


# -- bounded slots ---------------------------------------------------------


class _Doubler:
    """A "compression" that doubles its input, so a result's size is predictable."""

    info = PluginInfo("doubler", "Doubler", Stage.COMPRESSION)

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        return data[::2]

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        return bytes(b for byte in data for b in (byte, byte))


@pytest.fixture
def slotted(registry):
    registry.register(_Doubler())
    return registry


def _slot(length: int | None, fill=SlotFill.FILL) -> PathwayConfig:
    parent = b"\x11" * 64
    return PathwayConfig(
        FileRef(("rom.bin",), data=parent, offset=16, length=length),
        compression_id="doubler",
        slot_fill=fill,
        fill_byte=0x20,
        pathway="Z",
    )


def test_bounded_slot_refuses_a_longer_result(slotted):
    with pytest.raises(PipelineError) as caught:
        compress_for_slot(b"abcde", _slot(8), slotted, PipelineContext())
    assert "2 more than its 8-byte slot" in str(caught.value)
    assert caught.value.pathway == "Z"


def test_an_unrecorded_slot_is_bounded_by_the_end_of_the_buffer(slotted):
    """No recorded length is "to the end", not "as far as you like"."""
    ctx = PipelineContext()
    assert len(compress_for_slot(b"\x01" * 24, _slot(None), slotted, ctx)) == 48
    with pytest.raises(PipelineError) as caught:
        compress_for_slot(b"\x01" * 25, _slot(None), slotted, ctx)
    assert "2 more than the 48 bytes left in rom.bin" in str(caught.value)


def test_a_short_result_is_filled_or_kept(slotted):
    ctx = PipelineContext()
    filled = compress_for_slot(b"ab", _slot(8), slotted, ctx)
    assert filled == b"aabb" + b"\x20" * 4
    kept = compress_for_slot(b"ab", _slot(8, SlotFill.KEEP), slotted, ctx)
    assert kept == b"aabb"


def test_an_unbounded_short_result_is_not_padded(slotted):
    assert compress_for_slot(b"ab", _slot(None), slotted, PipelineContext()) == b"aabb"


def test_a_scheme_that_cannot_compress_refuses_the_write(registry):
    class _ReadOnly:
        info = PluginInfo("read_only", "Read only", Stage.COMPRESSION)

        def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
            return data

    registry.register(_ReadOnly())
    cfg = PathwayConfig(
        FileRef(("rom.bin",), data=b"x" * 8), compression_id="read_only"
    )
    with pytest.raises(PipelineError, match="cannot write back"):
        compress_for_slot(b"x", cfg, registry, PipelineContext())


# -- optional calls, inspection and scanning -------------------------------


def test_an_optional_hook_that_fails_degrades_with_a_notice(tmp_path, registry):
    class _Moody:
        info = PluginInfo("moody", "Moody", Stage.CONTAINER)

        def read(self, source, ctx):
            return source.data

        def header_size(self, source):
            raise RuntimeError("no idea")

    registry.register(_Moody())
    rom = tmp_path / "m.bin"
    rom.write_bytes(b"abcd")
    loaded = load(PathwayConfig(FileRef((str(rom),)), "moody"), registry)
    assert loaded.data == b"abcd"
    assert loaded.ctx.get(KEY_HEADER_SIZE) is None
    assert "could not answer header_size()" in loaded.ctx.notices[0].message


def test_container_report_names_what_the_read_published(tmp_path, registry):
    header = NES_MAGIC + bytes([1, 0, 0, 0]) + b"\x00" * 8
    rom = tmp_path / "g.nes"
    rom.write_bytes(header + b"\x00" * 32)
    report = inspect_container(PathwayConfig(FileRef((str(rom),)), "ines"), registry)
    assert not report.error
    assert report.payload_offset == 16 and report.payload_size == 32
    assert any(row.name == "header_size" for row in report.hints)


def test_container_report_reports_a_missing_file(tmp_path, registry):
    cfg = PathwayConfig(FileRef((str(tmp_path / "gone.bin"),)), "raw")
    assert inspect_container(cfg, registry).error


def test_find_next_structure_can_be_stopped(slotted):
    plugin = slotted.plugin(Stage.COMPRESSION, "doubler")
    seen: list[int] = []

    def tick(at: int) -> bool:
        seen.append(at)
        return True

    result = find_next_structure(
        b"\x00" * 512, plugin, 0, progress_every=8, on_tick=tick
    )
    assert result.stopped and result.found is None and result.end == 8
    assert seen == [8]


def test_write_target_room_matches_file_ref(tmp_path):
    """``WriteTarget.room()`` is ``FileRef.room()`` over the destination.

    A container is handed the slot as a target, never as the ref the host read
    it from, so the two have to agree about how much fits: whole-file, a sized
    slot, and a slot whose extent nobody recorded.
    """
    data = bytes(range(64))
    dest = tmp_path / "d.bin"
    dest.write_bytes(data)
    for offset, length in ((0, None), (16, 8), (16, None), (80, None)):
        ref = FileRef((str(dest),), offset=offset, length=length)
        target = WriteTarget(data, (str(dest),), offset, length)
        assert target.room() == ref.room(), (offset, length)
        assert target.whole_file is ref.whole_file
    assert WriteTarget(data).room() == len(data)
    assert WriteTarget(b"").room() == 0
