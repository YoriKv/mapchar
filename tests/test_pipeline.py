from __future__ import annotations

import time

import pytest

from helpers import ABC_TABLE, pointer_rom, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    PointerRef,
    PointerTableSource,
    RangeSource,
    WriteMode,
    block_bound,
    fill_run,
)
from mapchar.core.context import KEY_HEADER_SIZE, KEY_SOURCE_FILES, PipelineContext
from mapchar.core.errors import PipelineError
from mapchar.pipeline.extract import extract
from mapchar.pipeline.filechange import FileChange
from mapchar.pipeline.insert import apply_splices, layout_block, string_ends
from mapchar.pipeline.inspection import inspect_container
from mapchar.pipeline.pipeline import (
    FileRef,
    PathwayConfig,
    SlotFill,
    compress_for_slot,
    load,
    save,
)
from mapchar.pipeline.scan import (
    find_next_structure,
    find_structures,
    scheme_at,
    signature_of,
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
        b"\x00" * 512, [plugin], 0, progress_every=8, on_tick=tick
    )
    assert result.stopped and result.found is None and result.end == 8
    assert seen == [8]


def _rnc2_noise(count: int) -> bytes:
    """Bytes no RNC magic can hide in: ascending, so ``52 4E 43`` never adjoin."""
    return (bytes(range(256)) * (count // 256 + 1))[:count]


def _stray_rnc2() -> bytes:
    """``RNC\\x02`` with a header that is not one: the packed CRC decides."""
    return (
        b"RNC\x02"
        + (100).to_bytes(4, "big")
        + (20).to_bytes(4, "big")
        + b"\x12\x34"  # unpacked CRC
        + b"\x56\x78"  # packed CRC, which 20 bytes of 0xFF do not give
        + b"\x00\x01"
        + b"\xff" * 20
    )


def test_find_structures_takes_every_signed_stream_and_no_stray_magic(registry):
    """The signature says where to look; the scheme's own decoder decides.

    Two streams in noise, with a third ``RNC\\x02`` whose header fails its packed
    CRC between them: the walk finds the two, at the offsets they were laid at
    and with the compressed extent each declares.
    """
    from mapchar.plugins.builtins.compression import rnc

    first = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    second = rnc.compress(b"WORLD WORLD WORLD\x00" * 6, method=2)
    head = _rnc2_noise(64)
    middle = _stray_rnc2() + _rnc2_noise(32)
    data = head + first + middle + second + _rnc2_noise(48)
    at_second = len(head) + len(first) + len(middle)

    result = find_structures(data, [registry.plugin(Stage.COMPRESSION, "rnc2")])
    assert not result.stopped
    assert [(f.offset, f.consumed) for f in result.found] == [
        (len(head), len(first)),
        (at_second, len(second)),
    ]
    assert [f.scheme_id for f in result.found] == ["rnc2", "rnc2"]
    assert [f.size for f in result.found] == [8 * 18, 6 * 18]
    # Nothing scores the payloads unless the caller asks.
    assert [f.score for f in result.found] == [0.0, 0.0]
    scored = find_structures(
        data,
        [registry.plugin(Stage.COMPRESSION, "rnc2")],
        score=lambda payload: len(payload) / 1000,
    )
    assert [f.score for f in scored.found] == [0.144, 0.108]


def test_find_structures_reports_the_scheme_and_can_be_stopped(registry, slotted):
    """Both walks answer the same tick, and each scheme it finds is named."""
    from mapchar.plugins.builtins.compression import rnc

    stream = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    data = _rnc2_noise(32) + stream

    stopped = find_structures(
        data, [registry.plugin(Stage.COMPRESSION, "rnc2")], on_tick=lambda _at: True
    )
    # Stopped at the first candidate, and what it had found by then is kept.
    assert stopped.stopped and [f.offset for f in stopped.found] == [32]
    # A scheme with no signature is walked a byte at a time, as the forward scan
    # walks it, and answers the same tick.
    doubler = slotted.plugin(Stage.COMPRESSION, "doubler")
    assert find_structures(
        b"\x00" * 512, [doubler], on_tick=lambda _at: True, progress_every=8
    ).stopped


def test_scheme_at_arms_only_where_a_signature_reads_a_whole_structure(registry):
    from mapchar.plugins.builtins.compression import rnc

    stream = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    data = _rnc2_noise(16) + stream + _stray_rnc2()
    schemes = [
        registry.plugin(Stage.COMPRESSION, id) for id in ("rnc1", "rnc2", "gba_lz77")
    ]
    found = scheme_at(data, schemes, 16)
    assert found is not None
    plugin, structure = found
    assert plugin.info.id == "rnc2" and structure.complete
    assert structure.consumed == len(stream)
    # One byte off the signature, and on a header that fails its CRC: nothing.
    assert scheme_at(data, schemes, 17) is None
    assert scheme_at(data, schemes, 16 + len(stream)) is None
    # A scheme that announces itself in no way is never armed by looking.
    assert signature_of(registry.plugin(Stage.COMPRESSION, "gba_lz77")) == b""
    assert signature_of(registry.plugin(Stage.COMPRESSION, "rnc1")) == b"RNC\x01"


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


def test_a_file_change_holds_only_the_run_that_differs(tmp_path):
    """A write changes one region of a ROM, so that is all an undo step keeps;
    a single file may also grow or shrink, so the sizes travel with it."""
    before = b"\x00" * 8 + b"AB" + b"\xff" * 8
    after = b"\x00" * 8 + b"XYZ" + b"\xff" * 8
    change = FileChange.between("f", before, after)
    assert (change.offset, change.before, change.after) == (8, b"AB", b"XYZ")
    assert (change.before_size, change.after_size) == (18, 19)
    assert FileChange.between("f", before, before) is None
    assert change.flipped().flipped() == change


def test_a_file_change_moves_a_file_between_its_sides_and_no_further(tmp_path):
    before = b"\x00" * 8 + b"AB" + b"\xff" * 8
    after = b"\x00" * 8 + b"XYZ" + b"\xff" * 8
    dest = tmp_path / "d.bin"
    dest.write_bytes(before)
    change = FileChange.between(str(dest), before, after)
    assert change.holds_before() and not change.holds_after()
    assert change.apply() and dest.read_bytes() == after
    assert change.apply() and dest.read_bytes() == after  # already there
    assert change.flipped().apply() and dest.read_bytes() == before
    # Only the run the write changed is checked: a change elsewhere in the file
    # is no reason to refuse, but the run holding neither side has changed
    # since, and is left alone.
    dest.write_bytes(b"\x00" * 8 + b"AB" + b"\xfe" * 8)
    assert change.apply() and dest.read_bytes() == b"\x00" * 8 + b"XYZ" + b"\xfe" * 8
    dest.write_bytes(b"\x00" * 8 + b"QQ" + b"\xff" * 8)
    assert not change.apply()
    assert dest.read_bytes() == b"\x00" * 8 + b"QQ" + b"\xff" * 8


# --- The write path: a block's bound, and the pointers a packed write moves --

TS = table_set(ABC_TABLE, "main")

END_FF_TABLE = "@table main\n41=A\n42=B\n43=C\n/FF=[end]\n"
"""A table whose end token is the usual fill byte: ``FF`` is text here."""

TS_FF = table_set(END_FF_TABLE, "main")


def _pointer_block(start: int, stop: int, **kw) -> BlockConfig:
    source = PointerTableSource(start, stop, 2, 2, "little", "linear", 0)
    return BlockConfig(source, EndToken(), "main", **kw)


def test_a_default_bound_stops_where_the_fill_reads_as_text(registry):
    """The run after a pointer block's text is its own only where the block
    reads it as padding: a table that maps the fill byte reads it as text, and
    what follows is the next block's strings rather than room to take back.
    """
    data = bytearray(b"\x00" * 0x20)
    data[0:4] = bytes.fromhex("10 00 13 00")  # this block's strings
    data[4:10] = bytes.fromhex("16 00 17 00 18 00")  # the next block's
    data[0x10:0x1B] = bytes.fromhex("41 42 FF 42 41 FF FF FF 43 43 FF")
    rom = bytes(data)
    cfg, nxt = _pointer_block(0, 4), _pointer_block(4, 10)
    ex = extract(rom, cfg, TS_FF, registry)
    assert texts(ex) == ["AB[end]", "BA[end]"]
    assert texts(extract(rom, nxt, TS_FF, registry)) == ["[end]", "[end]", "CC[end]"]
    assert block_bound(cfg, ex.strings, rom, TS_FF) == 0x16
    # Two bytes longer, and it would land on the next block's first strings.
    res, _ = relayout(rom, cfg, TS_FF, {1: "BACA[end]"}, registry)
    assert not res.ok
    # A fill byte the table maps nothing with is padding, as it always was.
    plain = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=2)
    ex = extract(plain, cfg, TS, registry)
    assert block_bound(cfg, ex.strings, plain, TS) == len(plain)


def test_a_default_bound_counts_a_fill_repeat_cut_short(registry):
    """A fill pattern is laid from the start of the room it fills, so its last
    repeat may be cut short: an odd byte of it is padding too, and the room a
    shortening gave up is all there for the next edit."""
    rom = pointer_rom((0x10, 0x14), "41 42 43 00 42 41 43 00", tail=0) + b"\x77"
    cfg = _pointer_block(0, 4, fill=bytes.fromhex("FFFE"))
    res, out = relayout(rom, cfg, TS, {1: "BA[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x14:0x19] == bytes.fromhex("42 41 00 FF 77")
    assert block_bound(cfg, extract(out, cfg, TS, registry).strings, out, TS) == 0x18
    res, back = relayout(out, cfg, TS, {1: "BAC[end]"}, registry)
    assert res.ok, res.problems
    assert back == rom


def test_a_default_bound_stops_at_the_end_of_the_bytes(registry):
    """Text that runs to the end of the file has the fill after it and no
    more: the bound never reaches past the bytes there are."""
    rom = pointer_rom((0x10,), "41 42 00", tail=3)
    cfg = _pointer_block(0, 2)
    ex = extract(rom, cfg, TS, registry)
    assert block_bound(cfg, ex.strings, rom, TS) == len(rom) == 0x16
    res, out = relayout(rom, cfg, TS, {0: "ABABA[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x10:] == bytes.fromhex("41 42 41 42 41 00")
    assert len(out) == len(rom)
    # One byte more is one byte past the file.
    assert not relayout(rom, cfg, TS, {0: "ABABAB[end]"}, registry)[0].ok


def test_a_default_bound_over_a_long_fill_run_is_measured_not_walked(registry):
    """An expanded ROM's free space is where a relocated script lives, and the
    bound is asked for on every keystroke: the run is measured at once, not a
    pattern at a time."""
    rom = pointer_rom((0x10,), "41 42 00", tail=0) + b"\xff" * (1 << 20)
    cfg = _pointer_block(0, 2)
    ex = extract(rom, cfg, TS, registry)
    started = time.perf_counter()
    bounds = [block_bound(cfg, ex.strings, rom, TS) for _ in range(20)]
    assert bounds == [len(rom)] * 20
    assert time.perf_counter() - started < 1.0
    # Nor is the spare laid down again: the fill is already there, so the
    # splice stops where the block's text does.
    res, out = relayout(rom, cfg, TS, {0: "A[end]"}, registry)
    assert res.ok, res.problems
    # The new text, and the byte of the old it no longer covers.
    assert [(s.offset, len(s.data)) for s in res.splices] == [(0x10, 3), (0, 2)]
    assert out == rom[:0x10] + bytes.fromhex("41 00 FF") + rom[0x13:]


def test_a_packed_write_rewrites_the_pointers_attached_to_a_range_block(registry):
    """**Find Pointers ▸ Attach** puts the pointers it found on the strings of
    a range block without touching its source: a packed write moves the
    strings, so it rewrites those pointers like any others."""
    rom = pointer_rom((0x10, 0x14), "41 42 43 00 43 00", tail=0)
    cfg = BlockConfig(
        RangeSource(0x10, 0x16), EndToken(), "main", write_mode=WriteMode.PACKED
    )
    ex = extract(rom, cfg, TS, registry)
    assert texts(ex) == ["ABC[end]", "C[end]"]
    ex.strings[0].pointers = (PointerRef(0, 2, "little", "linear", 0, 0x10),)
    ex.strings[1].pointers = (PointerRef(2, 2, "little", "linear", 0, 0x14),)
    ex.strings[0].replacement = "A[end]"
    res = layout_block(rom, cfg, TS, ex.strings, registry)
    assert res.ok, res.problems
    out = apply_splices(rom, res.splices)
    assert out[0:4] == bytes.fromhex("10 00 12 00")
    assert out[0x10:0x16] == bytes.fromhex("41 00 43 00 FF FF")


def _banked_rom() -> bytes:
    """``A[end] B[end]`` at the very end of bank 1 of a ``banked`` ROM, with
    the two pointers that reach them at 0."""
    rom = bytearray(b"\xff" * 0x8010)
    rom[0:4] = bytes.fromhex("FC BF FE BF")
    rom[0x7FFC:0x8000] = bytes.fromhex("41 00 42 00")
    return bytes(rom)


def _banked_block() -> BlockConfig:
    source = PointerTableSource(0, 4, 2, 2, "little", "banked", 0, 1)
    return BlockConfig(source, EndToken(), "main")


def test_a_packed_write_refuses_a_pointer_that_cannot_reach_its_string(registry):
    """A short pointer reads in its block's bank, so a string pushed out of
    that bank cannot be pointed at: the write says so rather than leave a
    value that reads somewhere else entirely."""
    rom, cfg = _banked_rom(), _banked_block()
    assert texts(extract(rom, cfg, TS, registry)) == ["A[end]", "B[end]"]
    res, _ = relayout(rom, cfg, TS, {0: "AAA[end]"}, registry)
    assert not res.ok
    assert any("$2" in p.message for p in res.problems), res.problems
    # One byte less, and the string it moves is still in the bank.
    res, out = relayout(rom, cfg, TS, {0: "AA[end]"}, registry)
    assert res.ok, res.problems
    assert out[0:4] == bytes.fromhex("FC BF FF BF")
    assert texts(extract(out, cfg, TS, registry)) == ["AA[end]", "B[end]"]


class _ToyBanked:
    """A banked mapping that declares no ``needs_bank`` — absent is read as
    set — and no ``bank_of``: banks of $20 bytes mapped at $8000, the bank
    written above bit 16 where the pointer is wide enough to hold it."""

    info = PluginInfo("toy_banked", "Toy banked", Stage.MAPPING, "Test")
    sizes = (2, 3)

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        addr = value & 0xFFFF
        if value > 0xFFFF:
            bank = value >> 16
        return bank * 0x20 + (addr - 0x8000) if addr >= 0x8000 else None

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return ((offset // 0x20) << 16) | (0x8000 + offset % 0x20)


def test_a_packed_write_shortens_a_pointer_of_a_mapping_that_says_no_bank(registry):
    """A mapping that never says whether it needs a bank is taken to need one,
    as every other surface takes it: its pointers keep the bank the block is
    read in, and what makes that safe is that the value still reads back as
    the string it reaches."""
    registry.register(_ToyBanked())
    rom = bytearray(b"\xff" * 0x40)
    rom[0:4] = bytes.fromhex("00 80 03 80")
    rom[0x20:0x25] = bytes.fromhex("41 42 00 43 00")
    source = PointerTableSource(0, 4, 2, 2, "little", "toy_banked", 0, 1)
    cfg = BlockConfig(source, EndToken(), "main")
    assert texts(extract(bytes(rom), cfg, TS, registry)) == ["AB[end]", "C[end]"]
    res, out = relayout(bytes(rom), cfg, TS, {0: "A[end]"}, registry)
    assert res.ok, res.problems
    assert out[0:4] == bytes.fromhex("00 80 02 80")
    assert texts(extract(out, cfg, TS, registry)) == ["A[end]", "C[end]"]


@pytest.mark.parametrize("fill", [b"\xff\xfe", b"\xff\xfe\x7d"])
@pytest.mark.parametrize(
    "text, encoded", [("BA[end]", "42 41 00"), ("B[end]", "42 00"), ("[end]", "00")]
)
def test_a_packed_write_leaves_the_spare_as_one_run_of_the_fill(
    registry, fill, text, encoded
):
    """What the splice leaves standing has to be the fill the write would have
    laid there, phase and all: a multi-byte pattern carries on from where the
    text now ends, and a run that starts over out of step is not padding any
    more — the room would read as gone at the next edit."""
    rom = (
        pointer_rom((0x10, 0x14), "41 42 43 00 42 41 43 00", tail=0)
        + fill_run(fill, 8)
        + b"\x77"
    )
    cfg = _pointer_block(0, 4, fill=fill)
    ex = extract(rom, cfg, TS, registry)
    assert block_bound(cfg, ex.strings, rom, TS) == 0x20
    res, out = relayout(rom, cfg, TS, {1: text}, registry)
    assert res.ok, res.problems
    new = bytes.fromhex(encoded)
    assert out == rom[:0x14] + new + fill_run(fill, 0x20 - 0x14 - len(new)) + b"\x77"
    again = extract(out, cfg, TS, registry)
    assert block_bound(cfg, again.strings, out, TS) == 0x20
    # And the room is there to take back, to the byte.
    res, back = relayout(out, cfg, TS, {1: "BAC[end]"}, registry)
    assert res.ok, res.problems
    assert back == rom


def test_a_slot_stops_before_a_record_header_read_across_a_skip(registry):
    """A record header is stepped over the way the reading steps over it: with
    a skip range between one record and the next, the bytes the header takes
    are on both sides of the skip, and no slot may reach into them."""
    data = bytes.fromhex("05 CB 41 00 06 EE EE CB 42 00")
    cfg = BlockConfig(
        RangeSource(0, len(data)), EndToken(), "main", skips=((5, 7),), header=2
    )
    ex = extract(data, cfg, TS, registry)
    assert [(s.start, s.current_text()) for s in ex.strings] == [
        (2, "A[end]"),
        (8, "B[end]"),
    ]
    assert string_ends(data, cfg, ex.strings, registry, TS) == {0: 4, 1: 10}
    res, out = relayout(data, cfg, TS, {0: "B[end]"}, registry)
    assert res.ok, res.problems
    assert out == bytes.fromhex("05 CB 42 00 06 EE EE CB 42 00")
    # One byte longer would land on the next record's header.
    res, _ = relayout(data, cfg, TS, {0: "AB[end]"}, registry)
    assert not res.ok
