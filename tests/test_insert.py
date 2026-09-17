from __future__ import annotations

from helpers import ABC_TABLE, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    WriteMode,
)
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import encode_string

TS = table_set(ABC_TABLE, "main")


def test_slotted_default_without_pointers():
    data = bytes.fromhex("41 42 00 43 00 41 41")
    cfg = BlockConfig(RangeSource(0, 7), EndToken(), "main", fill=b"\xee")
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
        RangeSource(0, 7), EndToken(), "main", write_mode=WriteMode.PACKED, fill=b"\xee"
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
        fill=b"\xee",
    )
    res, out = relayout(data, cfg, TS, {0: "AA[end]"})
    # The untouched second string keeps its realignment padding bytes.
    assert res.ok and out == bytes.fromhex("41 41 00 EE 42 00 FF FF")


def test_fixed_strings_and_lines():
    data = bytes.fromhex("41 42 43 41 42 43")
    cfg = BlockConfig(
        RangeSource(0, 6), FixedLength(3), "main", line_length=2, fill=b"\xee"
    )
    # Line codes are dump formatting; the string is one fixed-length run.
    res, out = relayout(data, cfg, TS, {0: "B[line]\nC"})
    assert res.ok and out == bytes.fromhex("42 43 EE 41 42 43")
    res, out = relayout(data, cfg, TS, {0: "BBBB[line]C"})
    assert not res.ok and "too long" in res.problems[0].message
    cfg = BlockConfig(
        RangeSource(0, 6), FixedLength(3, True), "main", show_end=True, fill=b"\xee"
    )
    res, out = relayout(data, cfg, TS, {1: "A[end][end]\n"})
    assert res.ok and out == bytes.fromhex("41 42 43 41 00 EE")


def test_a_fixed_length_string_is_never_padded_past_its_slot():
    # The last run of a range the fixed length does not divide is shorter than
    # it. Padding to the full length would run into the neighbour — out is a
    # bytearray, so the slice assignment grows the buffer rather than stopping.
    data = bytes.fromhex("41 42 43 41 42")
    cfg = BlockConfig(RangeSource(0, 5), FixedLength(3), "main", fill=b"\xee")
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
        fill=b"\xee",
    )
    res, out = relayout(data, cfg, TS, {0: "A"})
    assert res.ok and out == bytes.fromhex("01 41 01 43 EE")


def test_lines():
    data = bytes.fromhex("41 FE 42 FE 43 FE 41 FE")
    cfg = BlockConfig(RangeSource(0, 8), Lines(2), "main", fill=b"\xee")
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


def test_a_slot_is_the_string_and_the_padding_after_it(registry):
    """Bytes between two pointed-to strings that are not the block's padding
    belong to no slot: an edit leaves them standing, and no string grows into
    them. The padding a shorter string left is the slot's, and grows back."""
    # Pointers to $4 and $9, A[end] and B[end], three bytes nothing points at
    # between them, and two more past the last string up to the bound.
    data = bytes.fromhex("04 00 09 00 41 00 5A 5A 5A 42 00 5A 5A")
    cfg = BlockConfig(
        PointerListSource((0, 2), 2),
        EndToken(),
        "main",
        bound=13,
        write_mode=WriteMode.SLOTTED,
        fill=b"\xee",
    )
    res, out = relayout(data, cfg, TS, {0: "A[end]", 1: "B[end]"}, registry)
    assert res.ok and out == data
    res, _ = relayout(data, cfg, TS, {0: "AB[end]"}, registry)
    assert not res.ok and res.problems[0].index == 0 and res.problems[0].over == 1
    # The same block once B[end] has been shortened to [end]: the fill byte
    # after it is padding, so it is room the next edit may use.
    padded = bytes.fromhex("04 00 09 00 41 00 5A 5A 5A 00 EE 5A 5A")
    res, out = relayout(padded, cfg, TS, {1: "B[end]"}, registry)
    assert res.ok and out == data


def test_a_slotted_splice_never_grows_the_buffer():
    """A bound past the end of the buffer bounds the last slot at the bytes
    there are; a splice past them would lengthen what it is spliced into."""
    data = bytes.fromhex("41 42 00 42 41 00")
    cfg = BlockConfig(RangeSource(0, 16), EndToken(), "main", fill=b"\xee")
    res, out = relayout(data, cfg, TS, {0: "A[end]"})
    assert res.ok and out == bytes.fromhex("41 00 EE 42 41 00")
    assert all(s.end <= len(data) for s in res.splices)


def test_a_next_pointer_string_reads_back_without_its_padding(registry):
    """A shorter replacement in a *next pointer* block leaves fill bytes in its
    slot. They are padding, not text, so the string reads back as what was
    typed -- and the slot stays whole, so it can grow back."""
    data = bytes.fromhex("06 00 09 00 FF FF") + b"ABCABC"
    cfg = BlockConfig(
        PointerTableSource(0, 4, 2, 2),
        NextPointer(),
        "main",
        write_mode=WriteMode.SLOTTED,
        fill=b"\xff",
    )
    res, out = relayout(data, cfg, TS, {0: "A"}, registry)
    assert res.ok and out == bytes.fromhex("06 00 09 00 FF FF 41 FF FF") + b"ABC"
    assert texts(extract(out, cfg, TS, registry)) == ["A", "ABC"]
    res, out = relayout(out, cfg, TS, {0: "ABC"}, registry)
    assert res.ok and out == data


def test_a_fixed_string_writes_its_end_token_where_there_is_room_then_fill():
    data = bytes.fromhex("41 42 00 EE DD EE  41 42 43 41 42 43")
    cfg = BlockConfig(
        RangeSource(0, 12), FixedLength(6, True), "main", fill=b"\xee\xdd"
    )
    res, out = relayout(data, cfg, TS, {0: "C", 1: "AB"})
    assert res.ok, res.problems
    assert out == bytes.fromhex("43 00 EE DD EE DD  41 42 00 EE DD EE")
    assert texts(extract(out, cfg, TS)) == ["C", "AB"]
    # No room for the end token: the text fills the string.
    res, out = relayout(data, cfg, TS, {0: "CCCCCC", 1: "BBBBB"})
    assert res.ok and out == bytes.fromhex("43 43 43 43 43 43  42 42 42 42 42 00")
    # A tail after the end token writes back as it reads.
    res, out = relayout(data, cfg, TS, {0: "A[end]C[$EE][$DD][$EE]"})
    assert res.ok and out[:6] == bytes.fromhex("41 00 43 EE DD EE")
    res, _ = relayout(data, cfg, TS, {0: "ABCABCA"})
    assert not res.ok and "too long" in res.problems[0].message


def test_fill_words_pad_slots_and_packed_tails_from_where_the_room_starts():
    data = bytes.fromhex("41 42 43 00 42 00")
    cfg = BlockConfig(RangeSource(0, 6), EndToken(), "main", fill=b"\xee\xdd")
    res, out = relayout(data, cfg, TS, {0: "A[end]"})
    assert res.ok and out == bytes.fromhex("41 00 EE DD 42 00")
    # The padding is the slot's again.
    res, out = relayout(out, cfg, TS, {0: "AAA[end]"})
    assert res.ok and out == bytes.fromhex("41 41 41 00 42 00")
    packed = BlockConfig(
        RangeSource(0, 6),
        EndToken(),
        "main",
        write_mode=WriteMode.PACKED,
        fill=b"\xee\xdd",
    )
    res, out = relayout(data, packed, TS, {0: "A[end]"})
    assert res.ok and out == bytes.fromhex("41 00 42 00 EE DD")
