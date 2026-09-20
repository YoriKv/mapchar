from __future__ import annotations

import time

import pytest

from helpers import ABC_TABLE, pointer_rom, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    NestedPointerSource,
    PointerRef,
    PointerTableSource,
    RangeSource,
    WriteMode,
    block_bound,
    remembered_room,
)
from mapchar.core.context import KEY_HEADER_SIZE, KEY_SOURCE_FILES, PipelineContext
from mapchar.core.errors import PipelineError
from mapchar.core.fill import fill_end, fill_run
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


# -- optional calls and inspection -----------------------------------------


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


def test_a_default_bound_is_the_end_of_the_text_the_pointers_reach(registry):
    """A pointer table bounds its pointers, not the text they reach: what a
    block with no bound of its own may write over is that text and no more.
    Bytes past it are the next block's, whatever they hold."""
    rom = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=8)
    cfg = _pointer_block(0, 4)
    ex = extract(rom, cfg, TS, registry)
    assert block_bound(cfg, ex.strings) == 0x16
    # The fill behind the text is not the block's to grow into.
    assert not relayout(rom, cfg, TS, {1: "BAC[end]"}, registry)[0].ok


def test_a_shortened_block_remembers_the_room_it_gave_up(registry):
    """What a write leaves the block remembering is the bound it had going in,
    and only where the text ends earlier than that."""
    rom = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=8)
    cfg = _pointer_block(0, 4)
    ex = extract(rom, cfg, TS, registry)
    assert remembered_room(cfg, 0x16, ex.strings) is None
    res, out = relayout(rom, cfg, TS, {1: "B[end]"}, registry)
    assert res.ok, res.problems
    after = extract(out, cfg, TS, registry).strings
    assert remembered_room(cfg, 0x16, after) == 0x16
    # A write that ends where the bound is — the same length, or back up to it
    # — leaves nothing to remember.
    assert remembered_room(cfg, 0x16, ex.strings) is None


def test_nothing_is_remembered_where_the_default_bound_does_not_apply(registry):
    """A configured bound and a range source say where the room ends
    themselves, and a nested source's groups are bounded one by one: none of
    them has room of its own to remember."""
    rom = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=8)
    strings = extract(rom, _pointer_block(0, 4), TS, registry).strings
    bounded = _pointer_block(0, 4, bound=0x20)
    assert remembered_room(bounded, 0x20, strings) is None
    ranged = BlockConfig(RangeSource(0x10, 0x20), EndToken(), "main")
    assert remembered_room(ranged, 0x20, strings) is None
    nested = BlockConfig(NestedPointerSource(0, 4, 2, 4), EndToken(), "main")
    assert remembered_room(nested, 0x20, strings) is None


def test_a_remembered_room_is_room_to_take_back(registry):
    """The room a shortened string gave up is the block's bound again, so
    typing the original back over it round-trips to the original bytes."""
    rom = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=8)
    cfg = _pointer_block(0, 4)
    res, out = relayout(rom, cfg, TS, {1: "B[end]"}, registry)
    assert res.ok, res.problems
    after = extract(out, cfg, TS, registry).strings
    room = remembered_room(cfg, 0x16, after)
    assert block_bound(cfg, after, room) == 0x16
    assert block_bound(cfg, after) == 0x15
    # Without the room there is nowhere to put the byte back.
    assert not relayout(out, cfg, TS, {1: "BA[end]"}, registry)[0].ok
    res, back = relayout(out, cfg, TS, {1: "BA[end]"}, registry, room=room)
    assert res.ok, res.problems
    assert back == rom


def test_a_second_shortening_keeps_the_first_room(registry):
    """Every write goes in under the bound the last one left, so the room a
    block gave up is remembered once and not given up twice."""
    rom = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=8)
    cfg = _pointer_block(0, 4)
    res, out = relayout(rom, cfg, TS, {1: "B[end]"}, registry)
    assert res.ok, res.problems
    once = extract(out, cfg, TS, registry).strings
    room = remembered_room(cfg, 0x16, once)
    res, again = relayout(out, cfg, TS, {1: "[end]"}, registry, room=room)
    assert res.ok, res.problems
    twice = extract(again, cfg, TS, registry).strings
    assert remembered_room(cfg, block_bound(cfg, once, room), twice) == 0x16
    res, back = relayout(again, cfg, TS, {1: "BA[end]"}, registry, room=0x16)
    assert res.ok and back == rom


def test_a_remembered_room_keeps_a_multi_byte_fill_s_last_repeat(registry):
    """A fill pattern is laid from the start of the room it fills, so its last
    repeat may be cut short: the odd byte is room too, and the room a
    shortening gave up is all there for the next edit."""
    rom = pointer_rom((0x10, 0x14), "41 42 43 00 42 41 43 00", tail=0) + b"\x77"
    cfg = _pointer_block(0, 4, fill=bytes.fromhex("FFFE"))
    res, out = relayout(rom, cfg, TS, {1: "BA[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x14:0x19] == bytes.fromhex("42 41 00 FF 77")
    after = extract(out, cfg, TS, registry).strings
    room = remembered_room(cfg, 0x18, after)
    assert room == 0x18 and block_bound(cfg, after, room) == 0x18
    res, back = relayout(out, cfg, TS, {1: "BAC[end]"}, registry, room=room)
    assert res.ok, res.problems
    assert back == rom


def test_a_block_never_takes_the_bytes_of_the_block_after_it(registry):
    """A block claims nothing on the strength of the bytes behind its text: a
    table whose end token is the fill byte reads an empty string and a byte of
    padding as the same byte, and the block after it holds three such strings.
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
    assert block_bound(cfg, ex.strings) == 0x16
    # Two bytes longer, and it would land on the next block's first strings.
    res, _ = relayout(rom, cfg, TS_FF, {1: "BACA[end]"}, registry)
    assert not res.ok
    assert rom[0x16:0x1B] == bytes.fromhex("FF FF 43 43 FF")
    # Shortened and lengthened again, its own room is still its own.
    res, out = relayout(rom, cfg, TS_FF, {1: "B[end]"}, registry)
    assert res.ok, res.problems
    after = extract(out, cfg, TS_FF, registry).strings
    room = remembered_room(cfg, 0x16, after)
    res, back = relayout(out, cfg, TS_FF, {1: "BA[end]"}, registry, room=room)
    assert res.ok and back == rom


FILL_MAPPED = table_set("@table main\nFF00=A\nFF01=B\n/FFFF=[end]\n", "main")
"""The Mother 3 shape: 16-bit codes over a table whose fill byte begins every
one of them, the end token included."""


def test_a_table_that_maps_the_fill_gets_its_room_back(registry):
    """Nothing is asked of the table: the room is the block's own extent, so a
    script whose every code begins with the fill byte takes back what its
    shortened strings gave up like any other."""
    rom = pointer_rom((0x10, 0x16), "FF00 FF01 FFFF FF01 FF00 FFFF", tail=4)
    cfg = _pointer_block(0, 4)
    ex = extract(rom, cfg, FILL_MAPPED, registry)
    assert texts(ex) == ["AB[end]", "BA[end]"]
    assert block_bound(cfg, ex.strings) == 0x1C
    res, out = relayout(rom, cfg, FILL_MAPPED, {1: "B[end]"}, registry)
    assert res.ok, res.problems
    after = extract(out, cfg, FILL_MAPPED, registry).strings
    room = remembered_room(cfg, 0x1C, after)
    assert room == 0x1C
    res, back = relayout(out, cfg, FILL_MAPPED, {1: "BA[end]"}, registry, room=room)
    assert res.ok, res.problems
    assert back == rom


def test_a_fill_run_counts_a_repeat_cut_short_and_stops_at_its_cap(registry):
    """What :func:`fill_run` would have laid there, and no byte more: whole
    patterns and a last one cut short, never past the cap a slot or a group
    gives it."""
    data = bytes.fromhex("41 FF FE FF 77")
    fill = bytes.fromhex("FFFE")
    assert fill_end(data, 1, fill) == 4
    assert fill_end(data, 1, fill, 3) == 3
    assert fill_end(data, 0, fill) == 0
    assert fill_end(data, 1, fill, 99) == 4


def test_a_fill_run_is_measured_not_walked(registry):
    """A slot's padding runs to the end of an expanded ROM's free space, and
    the slot is worked out on every keystroke: the run is measured at once,
    not a pattern at a time."""
    data = bytes.fromhex("41 42 00") + b"\xff" * (1 << 20)
    started = time.perf_counter()
    ends = [fill_end(data, 3, b"\xff") for _ in range(20)]
    assert ends == [len(data)] * 20
    assert time.perf_counter() - started < 1.0


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
    # Room the block gave up to an earlier shortening, so what refuses the
    # growth is the pointer rather than the bound.
    room = 0x8010
    res, _ = relayout(rom, cfg, TS, {0: "AAA[end]"}, registry, room=room)
    assert not res.ok
    assert any("$2" in p.message for p in res.problems), res.problems
    # One byte less, and the string it moves is still in the bank.
    res, out = relayout(rom, cfg, TS, {0: "AA[end]"}, registry, room=room)
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
    assert block_bound(cfg, ex.strings) == 0x18
    res, out = relayout(rom, cfg, TS, {1: text}, registry)
    assert res.ok, res.problems
    new = bytes.fromhex(encoded)
    assert out == rom[:0x14] + new + fill_run(fill, 0x18 - 0x14 - len(new)) + rom[0x18:]
    again = extract(out, cfg, TS, registry)
    room = remembered_room(cfg, 0x18, again.strings)
    assert room == 0x18 and block_bound(cfg, again.strings, room) == 0x18
    # A second shortening leaves one run as well, carried on from where its own
    # text ends rather than started over at the old text's end.
    res, twice = relayout(out, cfg, TS, {1: "[end]"}, registry, room=room)
    assert res.ok, res.problems
    assert twice == out[:0x14] + b"\x00" + fill_run(fill, 3) + out[0x18:]
    # And the room is there to take back, to the byte.
    res, back = relayout(twice, cfg, TS, {1: "BAC[end]"}, registry, room=room)
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
