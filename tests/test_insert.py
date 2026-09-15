from __future__ import annotations

from helpers import ABC_TABLE, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    Pascal,
    PointerListSource,
    RangeSource,
    WriteMode,
)
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import FileBlock, encode_string, lay_out_file

TS = table_set(ABC_TABLE, "main")


def test_slotted_default_without_pointers():
    data = bytes.fromhex("41 42 00 43 00 41 41")
    cfg = BlockConfig(RangeSource(0, 7), EndToken(), "main", fill=0xEE)
    assert cfg.effective_write_mode is WriteMode.SLOTTED
    res, out = relayout(data, cfg, TS, {0: "A[end]"})
    assert res.ok and out == bytes.fromhex("41 00 EE 43 00 41 41")
    res, out = relayout(data, cfg, TS, {1: "BB[end]"})
    assert not res.ok and res.problems[0].index == 1 and res.problems[0].over == 1
    res, out = relayout(data, cfg, TS, {0: "AB"})
    assert not res.ok and "end token" in res.problems[0].message


def test_packed_mode_with_bound():
    data = bytes.fromhex("41 42 00 43 00 41 41")
    cfg = BlockConfig(
        RangeSource(0, 7), EndToken(), "main", write_mode=WriteMode.PACKED, fill=0xEE
    )
    res, out = relayout(data, cfg, TS, {0: "A[end]", 1: "CC[end]"})
    # String 2 is untouched, so its original bytes (no end token) are reused.
    assert res.ok and out == bytes.fromhex("41 00 43 43 00 41 41")
    assert res.used == 7 and res.available == 7
    res, out = relayout(data, cfg, TS, {2: "AAAAA[end]"})
    assert not res.ok and res.problems[0].over == 4


def test_packed_realign():
    data = bytes.fromhex("41 00 FF FF 42 00 FF FF")
    cfg = BlockConfig(
        RangeSource(0, 8),
        EndToken(),
        "main",
        realign=(4, 0),
        write_mode=WriteMode.PACKED,
        fill=0xEE,
    )
    res, out = relayout(data, cfg, TS, {0: "AA[end]"})
    # The untouched second string keeps its realignment padding bytes.
    assert res.ok and out == bytes.fromhex("41 41 00 EE 42 00 FF FF")


def test_fixed_strings_and_lines():
    data = bytes.fromhex("41 42 43 41 42 43")
    cfg = BlockConfig(
        RangeSource(0, 6), FixedLength(3), "main", line_length=2, fill=0xEE
    )
    # Line codes are dump formatting; the string is one fixed-length run.
    res, out = relayout(data, cfg, TS, {0: "B[line]\nC"})
    assert res.ok and out == bytes.fromhex("42 43 EE 41 42 43")
    res, out = relayout(data, cfg, TS, {0: "BBBB[line]C"})
    assert not res.ok and "too long" in res.problems[0].message
    cfg = BlockConfig(
        RangeSource(0, 6), FixedLength(3, True), "main", show_end=True, fill=0xEE
    )
    res, out = relayout(data, cfg, TS, {1: "A[end][end]\n"})
    assert res.ok and out == bytes.fromhex("41 42 43 41 00 EE")


def test_a_fixed_length_string_is_never_padded_past_its_slot():
    # The last run of a range the fixed length does not divide is shorter than
    # it. Padding to the full length would run into the neighbour — out is a
    # bytearray, so the slice assignment grows the buffer rather than stopping.
    data = bytes.fromhex("41 42 43 41 42")
    cfg = BlockConfig(RangeSource(0, 5), FixedLength(3), "main", fill=0xEE)
    res, out = relayout(data, cfg, TS, {1: "C"})
    assert not res.ok and out is None
    assert res.problems[0].index == 1
    assert "short of the fixed length" in res.problems[0].message


def test_pascal():
    data = bytes.fromhex("02 41 42 01 43")
    cfg = BlockConfig(
        RangeSource(0, 5),
        Pascal(1),
        "main",
        write_mode=WriteMode.PACKED,
        bound=5,
        fill=0xEE,
    )
    res, out = relayout(data, cfg, TS, {0: "A"})
    assert res.ok and out == bytes.fromhex("01 41 01 43 EE")


def test_lines():
    data = bytes.fromhex("41 FE 42 FE 43 FE 41 FE")
    cfg = BlockConfig(RangeSource(0, 8), Lines(2), "main", fill=0xEE)
    res, out = relayout(data, cfg, TS, {0: "B[line]\n[line]"})
    assert res.ok and out == bytes.fromhex("42 FE FE EE 43 FE 41 FE")
    res, out = relayout(data, cfg, TS, {1: "A[line]"})
    assert not res.ok
    assert "holds 1 [line] code(s); the block reads 2" in res.problems[0].message


def test_untouched_strings_write_original_bytes():
    data = bytes.fromhex("41 99 00 42 00")
    cfg = BlockConfig(RangeSource(0, 5), EndToken(), "main")
    res, out = relayout(data, cfg, TS, {})
    assert res.ok and out == data


def test_a_bit_level_encoding_is_padded_to_the_byte(registry):
    """A five-bit table, as Dragon Warrior II's script uses: an edit whose bits
    stop short of the byte is padded with zero bits, as the ROM and Atlas pad
    it, since the pointer to the next string names a byte."""
    ts = table_set("@table main\n%00001=A\n%00010=B\n/%00000=[end]\n", "main")
    # Pointers to $4 and $6; A[end] and B[end], each ten bits padded to two bytes.
    data = bytes.fromhex("04 00 06 00 08 00 10 00")
    cfg = BlockConfig(
        PointerListSource((0, 2), 2, "little", "linear"), EndToken(), "main"
    )
    assert texts(extract(data, cfg, ts, registry)) == ["A[end]", "B[end]"]
    # AB[end] is fifteen bits: one bit of padding, two bytes, the same slot.
    res, out = relayout(data, cfg, ts, {0: "AB[end]"}, registry)
    assert res.ok, res.problems
    assert out == bytes.fromhex("04 00 06 00 08 80 10 00")
    assert texts(extract(out, cfg, ts, registry)) == ["AB[end]", "B[end]"]
    # ABB[end] is twenty bits, three bytes: it pushes B[end] over the bound.
    res, out = relayout(data, cfg, ts, {0: "ABB[end]"}, registry)
    assert not res.ok and res.problems[0].over == 1


def test_a_string_read_across_a_backwards_skip_keeps_both_pieces(registry):
    """The bytes of a string that jumped behind its start are the two runs it
    was read from -- ``length`` alone would count it as nothing."""
    ts = table_set(ABC_TABLE + "4341=X\n", "main")
    data = bytes.fromhex("04 00 08 00 41 00 42 00 43")
    cfg = BlockConfig(
        PointerListSource((0, 2), 2, "little", "linear"),
        EndToken(),
        "main",
        strings_per_pointer=2,
        skips=((9, 4),),
    )
    ex = extract(data, cfg, ts, registry)
    plain, wrapped = ex.strings
    assert plain.pieces(cfg.skips) == [(4, 8)]
    assert wrapped.pieces(cfg.skips) == [(8, 9), (4, 8)]
    assert wrapped.byte_length(cfg.skips) == 5
    assert encode_string(wrapped, cfg, ts, data).data == data[8:9] + data[4:8]
    res, out = relayout(data, cfg, ts, {}, registry)
    assert res.ok and out == data
    res, _ = relayout(data, cfg, ts, {1: "X[end]A[end]"}, registry)
    assert not res.ok and "skip range" in res.problems[0].message


def _file_block(key, data, cfg, edits, slot=None, payload=None):
    """One :class:`FileBlock` over ``data``, with ``{index: text}`` translated."""
    ex = extract(payload if payload is not None else data, cfg, TS)
    for i, text in edits.items():
        ex.strings[i].translation = text
    return FileBlock(
        key,
        str(key),
        cfg,
        TS,
        ex.strings,
        payload if payload is not None else data,
        slot,
    )


def test_lay_out_file_splices_each_uncompressed_block_into_the_file():
    data = bytes.fromhex("41 42 00 43 00 41 41") + bytes.fromhex("41 42 00")
    first = BlockConfig(RangeSource(0, 7), EndToken(), "main", fill=0xEE)
    second = BlockConfig(RangeSource(7, 10), EndToken(), "main", fill=0xEE)
    laid = lay_out_file(
        data,
        [
            _file_block("a", data, first, {0: "A[end]"}),
            _file_block("b", data, second, {0: "B[end]"}),
        ],
        recompress=lambda block, payload: (b"", "never asked"),
    )
    assert laid.ok and laid.problems == []
    assert laid.data == bytes.fromhex("41 00 EE 43 00 41 41 42 00 EE")
    # Neither reads its own buffer afterwards: both sit in the file itself.
    assert laid.written == {"a": None, "b": None}


def test_lay_out_file_compresses_a_shared_slot_once():
    """Two blocks over one compressed slot are laid into the same payload and
    the payload is packed once, so neither loses the other's edits."""
    payload = bytes.fromhex("41 42 00 43 00 41 41")
    cfg_a = BlockConfig(RangeSource(0, 3), EndToken(), "main", fill=0xEE)
    cfg_b = BlockConfig(RangeSource(3, 5), EndToken(), "main", fill=0xEE)
    packed: list[bytes] = []

    def recompress(block, buffer):
        packed.append(buffer)
        return b"<packed>", None

    laid = lay_out_file(
        b"\x00" * 4 + b"junkjunk",
        [
            _file_block("a", payload, cfg_a, {0: "B[end]"}, ("lz", 4), payload),
            _file_block("b", payload, cfg_b, {0: "A[end]"}, ("lz", 4), payload),
        ],
        recompress=recompress,
    )
    assert laid.ok, laid.problems
    # One recompress, over a payload carrying both edits.
    assert packed == [bytes.fromhex("42 00 EE 41 00 41 41")]
    assert laid.data == b"\x00" * 4 + b"<packed>"
    assert laid.written == {"a": packed[0], "b": packed[0]}


def test_lay_out_file_reports_a_block_that_will_not_fit_and_keeps_the_rest():
    data = bytes.fromhex("41 42 00 43 00 41 41")
    cfg = BlockConfig(RangeSource(0, 7), EndToken(), "main", fill=0xEE)
    laid = lay_out_file(
        data,
        [_file_block("a", data, cfg, {1: "BB[end]"})],
        recompress=lambda block, payload: (b"", None),
    )
    assert not laid.ok
    assert laid.problems == ["a #1: 1 byte(s) too long for its slot"]
    assert laid.data == data and laid.written == {}


def test_lay_out_file_reports_a_slot_that_will_not_compress():
    payload = bytes.fromhex("41 42 00 43 00 41 41")
    cfg = BlockConfig(RangeSource(0, 3), EndToken(), "main", fill=0xEE)
    laid = lay_out_file(
        b"\x00" * 8,
        [_file_block("a", payload, cfg, {0: "B[end]"}, ("lz", 0), payload)],
        recompress=lambda block, buffer: (b"", "lz: no room in the slot"),
    )
    assert laid.problems == ["lz: no room in the slot"]
    assert laid.data == b"\x00" * 8 and laid.written == {}
