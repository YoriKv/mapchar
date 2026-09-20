"""The LZ schemes: the BIOS LZ77, the LZSS framings, SLZ, the LZ1 and LZ2
command streams, PRS and Kosinski."""

from __future__ import annotations

import pytest

from compression_helpers import SCHEMES, decode, hexs
from mapchar.core.context import PipelineContext
from mapchar.plugins.builtins.compression import (
    GbaLz77,
    Kosinski,
    Lz1,
    Lz1Improved,
    Lz2,
    Lz2Improved,
    Prs,
    kosinski,
    prs,
)

# -- GBA/NDS BIOS LZ77 -------------------------------------------------------


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        # Flag 0x00: eight literals, of which the declared size wants four.
        ("10040000" + "00" + "41424344", b"ABCD"),
        # Flag 0x40: literal 'A', then bit 6 set -> a back reference of b0=b1=0,
        # i.e. length 3 at displacement field 0, which is distance *1*.
        ("10040000" + "40" + "41" + "0000", b"AAAA"),
        # Flag 0x20: 'A', 'B', then b0=0x10 -> length 4, displacement field 1 ->
        # distance 2. The copy overlaps what it is still writing.
        ("10060000" + "20" + "4142" + "1001", b"ABABAB"),
        # b0=0xF0 -> the maximum length of 18, at distance 1.
        ("10140000" + "20" + "5a5a" + "f000", b"Z" * 20),
    ],
)
def test_gba_lz77_decode_known_vector(stream: str, expected: bytes) -> None:
    out, consumed, complete = decode(GbaLz77(), hexs(stream))
    assert out == expected
    assert complete and consumed == len(hexs(stream))


def test_gba_lz77_declared_size_cuts_a_match_short() -> None:
    # The size is the only terminator and does not fall on match boundaries: the
    # last match here is 18 long where 17 are wanted.
    out, _, complete = decode(GbaLz77(), hexs("10130000", "20", "5a5a", "f000"))
    assert out == b"Z" * 19 and complete


def test_gba_lz77_rejects_what_is_not_a_structure() -> None:
    lz = GbaLz77()
    # The high nibble is the BIOS's dispatch; 0x30 is RLE, which this is not.
    with pytest.raises(ValueError, match="header"):
        decode(lz, hexs("30040000", "00", "41424344"))
    # The low nibble is reserved and carries stray values in real ROM data, so it
    # is masked off rather than required to be zero.
    assert decode(lz, hexs("1e040000", "00", "41424344"))[0] == b"ABCD"
    # A declared size of zero would make four bytes of noise a structure wherever
    # a 0x10 happened to land.
    with pytest.raises(ValueError, match="zero"):
        decode(lz, hexs("10000000", "00", "41424344"))
    # A reference before the start of the output has no defensible reading.
    with pytest.raises(ValueError, match="before the start"):
        decode(lz, hexs("10040000", "40", "41", "0001"))


@pytest.mark.parametrize(
    "data",
    [b"A", b"A" * 40, b"ABABAB" * 50, bytes(1024), bytes(range(256)) * 3, b"HI " * 300],
)
def test_gba_lz77_round_trips_and_stays_vram_safe(data: bytes) -> None:
    """Every emitted displacement must be at least 1, never 0.

    The VRAM-safe BIOS entry point (SWI 0x12) cannot handle a stored
    displacement of 0, and a stream carrying one still decodes perfectly under
    SWI 0x11 — so the constraint is invisible to a round trip and has to be
    asserted on the bytes.
    """
    lz = GbaLz77()
    stream = lz.compress(data, PipelineContext())
    out, consumed, complete = decode(lz, stream)
    assert out == data
    assert complete and consumed == len(stream)

    at = lz.header_size
    while at < len(stream):
        flags = stream[at]
        at += 1
        for bit in range(8):
            if at >= len(stream):
                break
            if flags & (0x80 >> bit):
                assert ((stream[at] & 0x0F) << 8) | stream[at + 1] >= 1
                at += 2
            else:
                at += 1


def test_gba_lz77_reports_the_structures_extent() -> None:
    payload = b"the quick brown fox " * 40
    lz = GbaLz77()
    stream = lz.compress(payload, PipelineContext())
    # Trailing bytes stand in for whatever follows the structure in a ROM: the
    # recorded size must be the stream's own, not the buffer it arrived in.
    out, consumed, complete = decode(lz, stream + b"\xff" * 64)
    assert out == payload and complete and consumed == len(stream)

    cut = stream[: len(stream) // 2]
    # Without the partial flag a cut-short buffer is an error, which is what lets
    # the UI tell "continues past the window" from "not a structure".
    with pytest.raises(ValueError, match="source ended"):
        decode(lz, cut)
    prefix, consumed, complete = decode(lz, cut, partial=True)
    assert payload.startswith(prefix) and 0 < len(prefix) < len(payload)
    assert not complete and consumed <= len(cut)


def test_gba_lz77_truncation_reports_the_last_complete_op() -> None:
    """A cut-short read consumes what it finished, not the buffer it was given.

    ``KEY_CONSUMED`` is what a block's length is backfilled from and what Jump to
    Next steps over, so claiming the whole tail would hand a block the rest of
    the file.
    """
    lz = GbaLz77()
    # A declared 32 bytes, a flags byte of eight literals, and two of them.
    out, consumed, complete = decode(lz, hexs("10200000", "00", "4142"), partial=True)
    assert out == b"AB" and consumed == 7 and not complete
    # The same, but the third op is a back reference with one of its two bytes:
    # the reference is not an op that completed, so it is not counted.
    out, consumed, _ = decode(lz, hexs("10200000", "20", "4142", "10"), partial=True)
    assert out == b"AB" and consumed == 7


# -- LZSS, 4 KiB ring --------------------------------------------------------


def test_lzss_ring_decode_known_vector() -> None:
    # Two literals then a distance-2 back reference of 5: ring position
    # (0xFEE + 0) & 0xFFF for output position 0, length 5 - 3 = 2.
    stream = hexs("07000000", "03", "4142", "eef2")
    out, consumed, complete = decode(SCHEMES["lzss_ring"], stream)
    assert out == b"ABABABA"  # "AB" + 5 bytes from position 0, overlapping
    assert (consumed, complete) == (len(stream), True)


def test_lzss_ring_reference_before_the_output_reads_the_zero_fill() -> None:
    # The ring is zero-filled and its cursor starts at 0xFEE, so a reference to a
    # position the output has not reached is legal and yields zeros. Getting the
    # origin wrong still decodes — it reads the wrong slots — so this pins 0xFEE.
    out, _, complete = decode(SCHEMES["lzss_ring"], hexs("03000000", "00", "0000"))
    assert out == b"\x00\x00\x00" and complete


def test_lzss_ring_size_prefix_bounds_the_decode() -> None:
    # The body carries no terminator: the declared size is the only thing that
    # ends it, and anything past the structure is the next reader's.
    out, consumed, complete = decode(
        SCHEMES["lzss_ring"], hexs("02000000", "03", "4142") + b"junk"
    )
    assert out == b"AB" and (consumed, complete) == (7, True)


def test_lzss_classic_has_no_end_to_find() -> None:
    """The Okumura framing carries no size, so no decode is ever complete.

    Reporting one would let a block created without a length backfill its extent
    from a decode that merely ran out of buffer.
    """
    lzss = SCHEMES["lzss_classic"]
    data = b"SPACE SPACE SPACE PADDED" * 4
    stream = lzss.compress(data, PipelineContext())
    out, consumed, complete = decode(lzss, stream)
    assert out == data
    assert complete is False and consumed == len(stream)
    # A truncated read is not an error either: there is no end it failed to find.
    short, _, complete = decode(lzss, stream[: len(stream) // 2])
    assert data.startswith(short) and complete is False
    # The ring is space-filled, so a reference before the output reads spaces.
    assert decode(lzss, hexs("00", "0000"))[0] == b"   "


# -- SLZ ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stream", "expected"),
    # This format puts the *distance* in the high twelve bits of the reference
    # word and biases it by 3, where the BIOS LZ77 puts the *length* in the high
    # nibble and biases the distance by 1. Read either way round a stream decodes
    # to something of about the right length.
    [
        # Token 0x00: eight literals, of which the declared size wants four.
        ("0004" + "00" + "41424344", b"ABCD"),
        # Token 0x10: three literals, then bit 3 set -> a reference of 0x0000,
        # i.e. distance 3 and length 3. Read as BIOS LZ77 this would be distance
        # 1, giving b"ABCCCC".
        ("0006" + "10" + "414243" + "0000", b"ABCABC"),
        # Token 0x08: four literals, then 0x0011 -> distance 4, length 4. Pins
        # the shift as well as the bias: a distance read from the low twelve bits
        # would be 0x011, not 1.
        ("0008" + "08" + "41424344" + "0011", b"ABCDABCD"),
        # Length 18 at distance 3: the copy overlaps what it is still writing.
        ("0015" + "10" + "414243" + "000f", b"ABC" * 7),
    ],
)
def test_slz16_decode_known_vector(stream: str, expected: bytes) -> None:
    out, consumed, complete = decode(SCHEMES["slz16"], hexs(stream))
    assert out == expected
    assert complete and consumed == len(hexs(stream))


def test_slz24_reads_the_same_body_behind_a_wider_prefix() -> None:
    # The variants differ in the size prefix and nothing else, so the same body
    # decodes identically — and a 16-bit reader would take the third prefix byte
    # for a token and produce nonsense rather than failing.
    raw = hexs("000004", "00", "41424344")
    assert decode(SCHEMES["slz24"], raw)[0] == b"ABCD"
    assert decode(SCHEMES["slz16"], raw)[0] != b"ABCD"


def test_slz_overshooting_the_declared_size_is_corrupt() -> None:
    # SLZ lands on its size exactly, so a match that overruns it says the tokens
    # were not the ones this stream was written with. Raised even under partial,
    # which forgives a short buffer only.
    stream = hexs("0004", "10", "414243", "000f")  # 3 literals + an 18-byte match
    with pytest.raises(ValueError, match="produced"):
        decode(SCHEMES["slz16"], stream)
    with pytest.raises(ValueError, match="produced"):
        decode(SCHEMES["slz16"], stream, partial=True)


def test_slz_empty_payload_is_a_zero_prefix() -> None:
    # The format's own encoding of an empty payload, unlike the size-prefixed
    # ring LZSS, where a zero would make any run of zero bytes a structure.
    out, consumed, complete = decode(SCHEMES["slz16"], hexs("0000") + b"junk")
    assert out == b"" and (consumed, complete) == (2, True)
    assert SCHEMES["slz16"].compress(b"", PipelineContext()) == hexs("0000")
    with pytest.raises(ValueError, match="zero"):
        decode(SCHEMES["lzss_ring"], hexs("00000000", "03", "4142"))


def test_slz16_rejects_a_payload_its_size_field_cannot_hold() -> None:
    with pytest.raises(ValueError, match="size field"):
        SCHEMES["slz16"].compress(bytes(0x10000), PipelineContext())


# -- LZ1 / LZ2 command streams ----------------------------------------------

_LZ_OUT = b"ABC" + b"DDDD" + b"XYX" + bytes((5, 6, 7)) + b"ABCD"
_LZ_BODY = [
    0x02, 0x41, 0x42, 0x43,  # literal x3: "ABC"
    0x23, 0x44,              # byte fill x4: "D"
    0x42, 0x58, 0x59,        # word fill x3: "XYX"
    0x62, 0x05,              # increasing fill x3: 5, 6, 7
    0x83, 0x00, 0x00,        # backref x4 @ 0 (the same either byte order)
    0xFF,                    # terminator
]  # fmt: skip
"""One command of each kind, then the terminator."""


def test_lz2_decode_known_vector() -> None:
    stream = bytes(_LZ_BODY)
    out, consumed, complete = decode(Lz2(), stream)
    assert out == _LZ_OUT
    assert complete and consumed == len(stream)


def test_lz1_offset_is_little_endian() -> None:
    # A backref at offset 0x0001 distinguishes the byte orders: LE reads
    # (0x01, 0x00); BE would read offset 0x0100 and fail on unwritten output.
    stream = bytes([0x01, 0x41, 0x42, 0x81, 0x01, 0x00, 0xFF])
    assert decode(Lz1(), stream)[0] == b"ABBB"
    with pytest.raises(ValueError):
        decode(Lz2(), stream)


def test_lz2_long_form_and_overlap() -> None:
    # Long-form byte fill of 300 zeros: header 111 001 LL, L = 299.
    encoded = 299
    stream = bytes([0xE0 | (0x20 >> 3) | (encoded >> 8), encoded & 0xFF, 0x00, 0xFF])
    assert decode(Lz2(), stream)[0] == bytes(300)
    # A backref reaching past the current output end re-reads its own output.
    stream = bytes([0x01, 0x11, 0x22, 0x85, 0x00, 0x00, 0xFF])
    assert decode(Lz2(), stream)[0] == bytes([0x11, 0x22] * 4)


def test_lz_command_partial_and_corruption_are_told_apart() -> None:
    payload = b"the quick brown fox " * 40
    full = Lz2().compress(payload, PipelineContext())
    cut = full[: len(full) // 2]
    with pytest.raises(ValueError, match="source exhausted"):
        decode(Lz2(), cut)
    prefix, _, complete = decode(Lz2(), cut, partial=True)
    assert payload.startswith(prefix) and not complete
    # Structural corruption raises even under the partial flag.
    with pytest.raises(ValueError, match="unwritten output"):
        decode(Lz2(), bytes([0x81, 0x10, 0x00, 0xFF]), partial=True)


def test_lz_improved_parse_is_smaller_and_both_decode_alike() -> None:
    data = b"Yoshi's Island " * 60 + bytes(range(64))
    ctx = PipelineContext()
    plain = Lz2().compress(data, ctx)
    improved = Lz2Improved().compress(data, ctx)
    assert len(improved) < len(plain)
    assert decode(Lz2(), improved)[0] == data
    assert decode(Lz1(), Lz1Improved().compress(data, ctx))[0] == data


# -- PRS ---------------------------------------------------------------------

_PRS_VECTOR = bytes([0x93, 0x41, 0x42, 0xFE, 0xD1, 0xFF, 0x02, 0x00, 0x00])
"""Literal 'A', literal 'B', a short copy (distance 2, length 4), a long copy
(distance 6, length 3), the end marker — and the interleave: the second control
byte sits *after* the long copy's operands, because a control byte is fetched
only when a bit from it is needed."""


def test_prs_decode_known_vector() -> None:
    out, consumed, complete = decode(Prs(), _PRS_VECTOR + b"trailing junk")
    assert out == b"ABABABABA"
    assert complete and consumed == len(_PRS_VECTOR)


def test_prs_control_bytes_interleave_lazily() -> None:
    # Nine distinct bytes are nine literals, so the first control byte's eight
    # bits run out mid-stream and the second lands *between* the eighth and ninth
    # literal rather than up front. A writer that emits control bytes eagerly
    # produces a stream this decoder will not read back.
    packed = prs.compress(bytes(range(9)))
    assert packed == bytes([0xFF]) + bytes(range(8)) + bytes([0x05, 0x08, 0x00, 0x00])
    assert decode(Prs(), packed)[0] == bytes(range(9))


def test_prs_truncation_and_corruption_are_told_apart() -> None:
    payload = b"the quick brown fox " * 40
    full = prs.compress(payload)
    cut = full[: len(full) // 2]
    with pytest.raises(ValueError):
        decode(Prs(), cut)
    prefix, _, complete = decode(Prs(), cut, partial=True)
    assert payload.startswith(prefix) and not complete
    # A copy reaching before the output raises whatever the flag says: a long
    # copy of distance 8191 (word 0x0008) with nothing yet written.
    with pytest.raises(ValueError, match="long copy reaches"):
        decode(Prs(), bytes([0x02, 0x08, 0x00, 0x05]), partial=True)


# -- Kosinski ----------------------------------------------------------------


def test_kosinski_descriptor_is_little_endian_lsb_first() -> None:
    # Three literals and the end marker in one partly-used descriptor word. Read
    # big-endian or MSB-first and the first bit selects a match instead of a
    # literal, so the stream decodes to garbage rather than failing.
    out, consumed, complete = decode(Kosinski(), hexs("170041424300F000"))
    assert out == b"ABC" and complete and consumed == 8


def test_kosinski_fetches_its_descriptor_before_the_pending_payload() -> None:
    # Sixteen literals spend a whole descriptor word, and the word for what
    # follows sits *before* the sixteenth literal's byte, because the decoder
    # fetches it the instant the last bit is spent. A decoder refilling lazily
    # reads the same bits in the same order and takes the 0x70 here as its word.
    stream = kosinski.compress(b"abcdefghijklmnop")
    assert stream == hexs("ffff6162636465666768696a6b6c6d6e6f02007000f000")
    assert decode(Kosinski(), stream)[0] == b"abcdefghijklmnop"


def test_kosinski_match_may_read_output_it_has_not_written_yet() -> None:
    # One literal plus a five-byte match one back is six bytes of fill: the copy
    # is a byte at a time, so the source repeats with period `distance`.
    assert decode(Kosinski(), hexs("5900AAFF00F000"))[0] == b"\xaa" * 6
    # Count 1 on the three-byte form is a module boundary: no output, not an end.
    assert decode(Kosinski(), hexs("16000000015A00F000"))[0] == b"Z"


def test_kosinski_truncation_needs_the_partial_flag() -> None:
    plain = b"the quick brown fox " * 40
    stream = kosinski.compress(plain)
    cut = stream[: len(stream) // 2]
    with pytest.raises(ValueError):
        decode(Kosinski(), cut)
    prefix, _, complete = decode(Kosinski(), cut, partial=True)
    assert plain.startswith(prefix) and not complete
    with pytest.raises(ValueError, match="reaches"):
        decode(Kosinski(), hexs("0C00FF00F000"))
