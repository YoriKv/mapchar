"""The compression stage: known byte vectors first, then the round trips.

A vector is what a round trip cannot catch. Every scheme here has at least one
field that fails *silently* when read the wrong way round — which end of a flag
byte the first op sits at, whether a set bit means the reference or the literal,
which nibble holds the length, what a distance is biased by — and a compressor
that makes the same mistake as its decompressor round-trips perfectly. So each
scheme is pinned to bytes assembled from its format description, and only then
asked to round-trip.
"""

from __future__ import annotations

import random

import pytest

from conftest import ROOT
from mapchar.core.context import (
    KEY_COMPLETE,
    KEY_CONSUMED,
    KEY_DECOMPRESS_PARTIAL,
    PipelineContext,
)
from mapchar.plugins.base import Stage, writes_back
from mapchar.plugins.builtins.compression import (
    BitPack,
    GbaLz77,
    HuffmanTable,
    KonamiFdsRle,
    KonamiNesRle,
    Kosinski,
    Lz1,
    Lz1Improved,
    Lz2,
    Lz2Improved,
    Lzss,
    PackBits,
    Prs,
    Rle1,
    Rle2,
    Rnc1,
    Rnc2,
    konami_rle,
    kosinski,
    prs,
    rnc,
    snes_rle,
)
from mapchar.plugins.builtins.compression import packbits as packbits_mod
from mapchar.plugins.builtins.compression._limits import MAX_BANK, MAX_OUT
from mapchar.plugins.builtins.compression.lzss import PRESET_LZSS, presets

SCHEMES = {p.info.id: p for p in presets()}
"""The LZSS family by plugin id, so a test names a framing as the UI does."""


def decode(plugin, data: bytes, *, partial: bool = False):
    """``(output, consumed, complete)`` through the stage, as the pipeline runs it."""
    ctx = PipelineContext()
    if partial:
        ctx.set(KEY_DECOMPRESS_PARTIAL, True)
    out = plugin.decompress(data, ctx)
    return out, ctx.get(KEY_CONSUMED), ctx.get(KEY_COMPLETE)


def hexs(*parts: str) -> bytes:
    return bytes.fromhex("".join(parts))


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


# -- RNC ---------------------------------------------------------------------


def _rnc_stream(method: int, body: bytes, plain: bytes, chunks: int = 1) -> bytes:
    """A header around ``body``, with the sizes and CRCs the decoder verifies."""
    return (
        b"RNC"
        + bytes((method,))
        + len(plain).to_bytes(4, "big")
        + len(body).to_bytes(4, "big")
        + rnc.crc16(plain).to_bytes(2, "big")
        + rnc.crc16(body).to_bytes(2, "big")
        + bytes((0, chunks))
        + body
    )


def _lsb_words(fields: list[tuple[int, int]]) -> bytes:
    """``(value, bits)`` fields packed low bit first into 16-bit LE words."""
    acc = shift = 0
    for value, bits in fields:
        acc |= value << shift
        shift += bits
    return acc.to_bytes(-(-shift // 16) * 2, "little")


def test_rnc_crc16_is_the_reflected_a001_polynomial() -> None:
    assert rnc.crc16(b"123456789") == 0xBB3D


def test_rnc1_decodes_length_classes_and_ends_a_chunk_on_a_literal_run() -> None:
    """Two literals, a 6-byte match 2 back, and an empty final run.

    Guards three silent traps at once: a Huffman symbol is a *bit-length class*
    followed by extra bits (the run of 2 is symbol 2 plus one bit, the length 6
    is symbol 3 plus two); a subchunk count of 2 is two runs but one match; and
    the literal bytes sit after the whole 16-bit word their run code is in, not
    inside the bit stream.
    """
    bits = _lsb_words(
        [
            (0, 1), (0, 1),                      # not locked, not keyed
            (3, 5), (1, 4), (0, 4), (1, 4),      # runs: symbols 0 and 2, 1 bit
            (2, 5), (0, 4), (1, 4),              # distances: symbol 1 only
            (4, 5), (0, 4), (0, 4), (0, 4), (1, 4),  # lengths: symbol 3 only
            (2, 16),                             # two subchunks
            (1, 1), (0, 1),                      # run: symbol 2, extra 0 -> 2
            (0, 1),                              # distance: 1 + 1
            (0, 1), (0, 2),                      # length: symbol 3, extra 0 -> 4 + 2
            (0, 1),                              # final run: 0
        ]
    )  # fmt: skip
    plain = b"ABABABAB"
    stream = _rnc_stream(1, bits + b"AB", plain)
    assert decode(Rnc1(), stream) == (plain, len(stream), True)


def test_rnc2_reads_raw_bytes_between_bit_bytes() -> None:
    """Literal A, literal B, a 4-byte match 2 back, then the end of the chunk.

    The bit bytes are 0x08 and 0x78; the two literals follow the first because
    it was fetched before they were read, and the match's distance byte and the
    marker's zero byte follow the second for the same reason.
    """
    stream = _rnc_stream(2, hexs("084142780100"), b"ABABAB")
    assert decode(Rnc2(), stream) == (b"ABABAB", len(stream), True)


@pytest.mark.parametrize("method", [rnc.METHOD_1, rnc.METHOD_2])
@pytest.mark.parametrize(
    "plain",
    # Nothing, one byte, incompressible bytes, a long fill, a repeat 2000 back,
    # and low-entropy data past the 0x3000-byte chunk the packer covers.
    [
        b"",
        b"A",
        bytes(range(256)),
        b"\xab" * 5000,
        bytes((i * 7919) % 251 for i in range(2000)) * 2,
        bytes((i * 97 + i // 7) & 0xFF for i in range(0x3100)),
    ],
)
def test_rnc_round_trips(method: int, plain: bytes) -> None:
    """Both directions, and the extent the header makes exact.

    ``consumed`` is the header plus its packed size, whatever follows in the
    buffer, which is what lets Scan step from one stream to the next.
    """
    stream = rnc.compress(plain, method=method)
    plugin = Rnc1() if method == rnc.METHOD_1 else Rnc2()
    out, consumed, complete = decode(plugin, stream + b"RNC junk")
    assert out == plain
    assert consumed == len(stream)
    assert complete is True


def test_rnc_rejects_corrupt_streams_and_forgives_only_a_short_buffer() -> None:
    rng = random.Random(5)
    plain = bytes(rng.randrange(256) for _ in range(1500)) * 2
    stream = rnc.compress(plain, method=1)

    flipped = bytearray(stream)
    flipped[40] ^= 0x10
    with pytest.raises(ValueError, match="packed data fails its CRC"):
        decode(Rnc1(), bytes(flipped))
    wrong_sum = bytearray(stream)
    wrong_sum[12] ^= 1
    with pytest.raises(ValueError, match="unpacked data fails its CRC"):
        decode(Rnc1(), bytes(wrong_sum))
    with pytest.raises(ValueError, match="method byte"):
        decode(Rnc2(), stream)

    cut = stream[: len(stream) // 2]
    with pytest.raises(ValueError, match="source ends"):
        decode(Rnc1(), cut)
    prefix, consumed, complete = decode(Rnc1(), cut, partial=True)
    assert not complete
    assert 0 < len(prefix) < len(plain)
    assert plain.startswith(prefix)
    assert consumed <= len(cut)


def test_every_rnc2_stream_in_the_mk2_rom_unpacks_to_its_declared_size() -> None:
    """The 29 streams Mortal Kombat II (GB) carries, when the ROM is present.

    The ROM never enters the repository; without it this skips. What it pins is
    the decoder against a real packer's output rather than this module's own:
    every ``RNC\\x02`` whose packed CRC checks out has to reach its declared
    unpacked size and report the header's extent.
    """
    rom = ROOT / "sample-projects" / "MK2" / "Mortal Kombat II (USA, Europe).gb"
    if not rom.exists():
        pytest.skip("the Mortal Kombat II ROM is not present")
    data = rom.read_bytes()
    found = 0
    at = data.find(b"RNC\x02")
    while at >= 0:
        declared = int.from_bytes(data[at + 4 : at + 8], "big")
        packed = int.from_bytes(data[at + 8 : at + 12], "big")
        end = at + rnc.HEADER_SIZE + packed
        stored = int.from_bytes(data[at + 14 : at + 16], "big")
        if end <= len(data) and rnc.crc16(data[at + rnc.HEADER_SIZE : end]) == stored:
            out, consumed, complete = decode(Rnc2(), data[at:])
            assert len(out) == declared, hex(at)
            assert complete and consumed == end - at, hex(at)
            found += 1
        at = data.find(b"RNC\x02", at + 1)
    assert found == 29


def test_rnc_refuses_a_target_over_the_cap_a_keyed_stream_and_an_empty_chunk() -> None:
    """Three headers and flags that are refused before any output is made."""
    over_cap = (
        b"RNC\x02"
        + (MAX_OUT + 1).to_bytes(4, "big")
        + (2).to_bytes(4, "big")
        + b"\x00\x00\x00\x00\x00\x01"
    )
    with pytest.raises(ValueError, match="past the"):
        decode(Rnc2(), over_cap + b"\x00\x00", partial=True)
    # The second flag bit is the key: the data alone can never be unpacked, so a
    # keyed stream is refused rather than decoded to noise.
    with pytest.raises(ValueError, match="encrypted"):
        decode(Rnc2(), _rnc_stream(2, b"\x40", b"A"))
    with pytest.raises(ValueError, match="encrypted"):
        decode(Rnc1(), _rnc_stream(1, _lsb_words([(0, 1), (1, 1)]), b"A"))
    # Method 1's three tables, then a subchunk count of zero: a chunk that
    # produces nothing and would be read again forever.
    tables = [
        (0, 1), (0, 1),
        (3, 5), (1, 4), (0, 4), (1, 4),
        (2, 5), (0, 4), (1, 4),
        (4, 5), (0, 4), (0, 4), (0, 4), (1, 4),
    ]  # fmt: skip
    empty = _rnc_stream(1, _lsb_words([*tables, (0, 16)]), b"ABABABAB")
    with pytest.raises(ValueError, match="no subchunks"):
        decode(Rnc1(), empty)


def test_rnc_partial_decode_never_reports_a_stream_the_buffer_does_not_hold() -> None:
    """A buffer that stops short of the packed size holds no whole structure.

    Its packed CRC was never checked and its last bytes were never read, so the
    decode is a prefix however much of the payload it managed — and ``consumed``
    is where the buffer ends, never the position past it the header declares.
    """
    # A trailing pad byte inside the packed size: the decoder reaches the
    # declared output before it, so a buffer cut there decodes in full anyway.
    stream = _rnc_stream(2, hexs("084142780100") + b"\x00", b"ABABAB")
    cut = stream[:-1]
    with pytest.raises(ValueError, match="source ends"):
        decode(Rnc2(), cut)
    out, consumed, complete = decode(Rnc2(), cut, partial=True)
    assert out == b"ABABAB"
    assert complete is False and consumed == len(cut)
    # And the ordinary cut, inside a literal run: the bytes that arrived, and
    # the position they stopped at.
    plain = bytes((i * 7919) % 251 for i in range(600))
    whole = rnc.compress(plain, method=2)
    short = whole[: len(whole) // 2]
    prefix, consumed, complete = decode(Rnc2(), short, partial=True)
    assert plain.startswith(prefix) and 0 < len(prefix) < len(plain)
    assert complete is False and consumed <= len(short)


def test_find_structures_reads_the_edges_of_the_buffer(registry) -> None:
    """Three things the whole-file walk meets at a buffer's end.

    A signature in the last few bytes, a stream the file stops inside, and two
    streams with nothing between them: the first two are not structures, the
    walk still ends, and the third pair is found at both offsets.
    """
    from mapchar.pipeline.scan import find_structures

    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    first = rnc.compress(b"HELLO HELLO HELLO\x00" * 6, method=2)
    second = rnc.compress(b"WORLD WORLD WORLD\x00" * 4, method=2)

    assert find_structures(b"\xff" * 32 + b"RNC\x02", [rnc2]).found == []
    assert find_structures(b"\xff" * 16 + first[:-4], [rnc2]).found == []

    both = find_structures(first + second, [rnc2]).found
    assert [(f.offset, f.consumed) for f in both] == [
        (0, len(first)),
        (len(first), len(second)),
    ]


def test_find_next_structure_walks_the_schemes_it_is_given(registry) -> None:
    """The nearest structure of any of them, from the one walk.

    The forward scan takes the schemes to consider like the other two probes,
    so the Decompressed View's Scan is the picked scheme on a pick and every
    scheme that announces itself on automatic.
    """
    from mapchar.pipeline.scan import find_next_structure

    rnc1 = registry.plugin(Stage.COMPRESSION, "rnc1")
    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    one = rnc.compress(b"HELLO HELLO HELLO\x00" * 4, method=1)
    two = rnc.compress(b"WORLD WORLD WORLD\x00" * 4, method=2)
    data = b"\xff" * 8 + two + b"\xff" * 8 + one + b"\xff" * 8
    at_two, at_one = 8, 8 + len(two) + 8

    assert find_next_structure(data, [rnc1], 0).found == at_one
    assert find_next_structure(data, [rnc2], 0).found == at_two
    assert find_next_structure(data, [rnc1, rnc2], 0).found == at_two
    assert find_next_structure(data, [rnc1, rnc2], at_two + 1).found == at_one
    # Nothing to walk for is not a walk that found nothing: both end the same
    # way, and the caller is what tells them apart.
    assert find_next_structure(data, [], 0).found is None


def _stray_rnc2() -> bytes:
    """An ``RNC\\x02`` header no decode gets past: its packed CRC is nothing the
    bytes behind it give."""
    return (
        b"RNC\x02"
        + (100).to_bytes(4, "big")
        + (50).to_bytes(4, "big")
        + b"\x12\x34\x56\x78\x00\x01"
    )


def test_find_next_structure_looks_where_a_signature_says_to(registry) -> None:
    """A whole ROM's worth of bytes, walked in the time a byte scan takes.

    A strict decode at every offset of a 4 MB buffer is minutes of work, and
    the scan behind the Decompressed View's Scan button runs it on a keypress.
    A scheme that announces itself is only decoded where its signature sits, so
    the walk is `bytes.find` plus a decode per stray magic.
    """
    import time

    from mapchar.pipeline.scan import find_next_structure

    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    stream = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    stray = _stray_rnc2()
    head = b"\xff" * 0x100000 + stray + b"\xff" * 0x100000 + stray
    data = head + b"\xff" * 0x200000 + stream + b"\xff" * 0x100
    at = len(head) + 0x200000

    started = time.perf_counter()
    result = find_next_structure(data, [rnc2], 0)
    assert result.found == at and not result.stopped
    assert time.perf_counter() - started < 1.0
    # And Stop still reaches in, on the first offset worth a decode rather than
    # after a megabyte of walking.
    seen: list[int] = []

    def tick(pos: int) -> bool:
        seen.append(pos)
        return True

    stopped = find_next_structure(data, [rnc2], 0, on_tick=tick)
    assert stopped.stopped and stopped.found is None
    # The first stray magic, decoded and stepped past: a tick a megabyte in
    # rather than after a megabyte of walking.
    assert seen == [0x100000 + 1]


def test_find_next_structure_mixes_schemes_that_announce_themselves_and_not(
    registry,
) -> None:
    """A signature jumps, no signature walks, and the nearest structure wins."""
    from mapchar.pipeline.scan import find_next_structure

    gba = registry.plugin(Stage.COMPRESSION, "gba_lz77")
    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    packed = gba.compress(b"HELLO HELLO HELLO\x00" * 4, PipelineContext())
    stream = rnc.compress(b"WORLD WORLD WORLD\x00" * 4, method=2)
    # 0xFF starts no structure of either scheme, so only the two laid here do.
    data = b"\xff" * 64 + packed + b"\xff" * 64 + stream + b"\xff" * 64
    at_packed, at_stream = 64, 64 + len(packed) + 64

    assert find_next_structure(data, [gba], 0).found == at_packed
    assert find_next_structure(data, [rnc2], 0).found == at_stream
    assert find_next_structure(data, [rnc2, gba], 0).found == at_packed
    assert find_next_structure(data, [rnc2, gba], at_packed + 1).found == at_stream


# -- PackBits ----------------------------------------------------------------


def test_packbits_decode_known_vector() -> None:
    # The worked example from the format's own documentation. Guards the signed
    # control arithmetic (0xFE = -2 is a *3*-byte run) and pins that a literal
    # payload may hold 0x80 verbatim.
    stream = hexs("fe aa 02 80 00 2a fd aa 03 80 00 2a 22 f7 aa".replace(" ", ""))
    out, consumed = packbits_mod.decompress(stream)
    assert out == hexs("aaaaaa80002aaaaaaaaa80002a22aaaaaaaaaaaaaaaaaaaa")
    assert consumed == len(stream)
    # Our encoder reproduces this stream byte for byte: every run is >= 3, so the
    # run/literal split has no leeway.
    assert packbits_mod.compress(out) == stream


def test_packbits_is_never_complete_and_stops_at_the_cap() -> None:
    data = b"AAAAAAAABCDEFFFFFFFFFFFFG" + b"\x00" * 300
    packed = PackBits().compress(data, PipelineContext())
    out, consumed, complete = decode(PackBits(), packed)
    assert out == data and consumed == len(packed)
    # Any bytes are valid PackBits, so "the buffer ran out" is not "the structure
    # ended" and a Scan must not be able to find one.
    assert complete is False
    # 128 output bytes per 2 input bytes: the cap stops the decode at a packet
    # boundary rather than raising, there being no stream end to contradict.
    bomb = bytes([0x81, 0x00]) * 20000
    out, consumed = packbits_mod.decompress(bomb)
    assert len(out) == MAX_OUT
    assert consumed == 2 * (MAX_OUT // 128) < len(bomb)
    # A packet cut off by the buffer's end contributes the bytes that arrived,
    # and consumes nothing: `consumed` may never reach past the buffer.
    out, consumed = packbits_mod.decompress(bytes([0x05, 0x41]))
    assert out == b"A" and consumed == 0


# -- RLE1 / RLE2 -------------------------------------------------------------


def test_rle1_decode_known_vector() -> None:
    # A 3-byte literal (0x02 = L + 1), a 5-run (0x84 = C set, L + 1 = 5), a
    # 1-byte literal, then the $FF $FF end. Guards the L + 1 arithmetic and pins
    # that $FF ends the stream only at a *header* position — the 5-run's value
    # byte here is $FF and must not end it.
    stream = hexs("02414243", "84ff", "007a", "ffff")
    out, consumed, complete = decode(Rle1(), stream)
    assert out == b"ABC" + b"\xff" * 5 + b"\x7a"
    assert complete and consumed == len(stream)
    # The same bytes without the terminator are RLE2, and never complete.
    out2, consumed2, complete2 = decode(Rle2(), stream[:-2])
    assert out2 == out and consumed2 == len(stream) - 2 and complete2 is False


def test_rle1_needs_its_terminator() -> None:
    data = bytes(range(200))
    stream = snes_rle.compress(data, terminated=True)
    with pytest.raises(ValueError, match="terminator"):
        decode(Rle1(), stream[:-10])
    prefix, _, complete = decode(Rle1(), stream[:-10], partial=True)
    assert data.startswith(prefix) and not complete
    # 128 copies of $FF would spell the terminator, so a run of that one byte
    # stops a packet short instead.
    assert decode(Rle1(), snes_rle.compress(b"\xff" * 300, terminated=True))[0] == (
        b"\xff" * 300
    )


def test_rle2_stops_at_the_output_cap() -> None:
    bomb = b"\xff\x00" * MAX_BANK  # 128-runs, two bytes each
    out, consumed, complete = snes_rle.decompress(bomb, terminated=False)
    assert len(out) == MAX_BANK
    assert consumed == 2 * (MAX_BANK // 128) < len(bomb)
    assert complete is False


# -- Konami RLE --------------------------------------------------------------


def test_konami_decode_known_vector() -> None:
    # One fill, one literal, a 0x7F PPU address change (the next 2 bytes are the
    # little-endian destination — consumed, not emitted), a second fill, then the
    # terminator. Pins the address-change skip: the address low byte 0x34 must
    # not be mistaken for a fill-52 control.
    stream = hexs("03aa", "821122", "7f3412", "02bb", "ff")
    out, consumed, complete = decode(KonamiNesRle(), stream + b"\x11" * 7)
    assert out == hexs("aaaaaa", "1122", "bbbb")
    assert complete and consumed == len(stream)


def test_konami_fds_reads_the_reserved_controls_differently() -> None:
    # One stream, two readings. After a fill both agree on, 0x7F diverges: the
    # Contra reading skips a 2-byte address, the FDS one reads a 127-fill.
    stream = hexs("0230", "7f41", "01ff", "ff")
    # Contra: the 0x7F consumes "41 01" as a destination address and emits
    # nothing; FDS: a 127-fill of 0x41, then a 1-fill of 0xFF.
    assert decode(KonamiNesRle(), stream)[0] == b"00"
    assert decode(KonamiFdsRle(), stream)[0] == b"00" + b"A" * 127 + b"\xff"
    # FDS 0x80 is a 256-byte literal, whose payload spans the control values.
    literal = bytes(range(256))
    out, _, complete = decode(KonamiFdsRle(), hexs("80") + literal + hexs("ff"))
    assert out == literal and complete


def test_konami_fds_decode_known_vector() -> None:
    # Both reserved controls in one stream, interleaved with an ordinary fill and
    # literal: a 127-fill of 0xCC, then a 256-byte literal whose payload carries
    # control values (0x7F, 0x80, 0xFF) verbatim, then the shared terminator.
    # Fixed bytes, so the FDS reading is pinned whatever our compressor emits.
    literal = bytes(range(256))
    stream = hexs("03aa", "821122", "7fcc", "80") + literal + hexs("ff")
    out, consumed, complete = decode(KonamiFdsRle(), stream)
    assert out == hexs("aaaaaa", "1122") + b"\xcc" * 127 + literal
    assert complete and consumed == len(stream)


def test_konami_truncation_needs_the_partial_flag() -> None:
    data = bytes(range(200))
    packed = konami_rle.compress(data)
    cut = packed[:-30]
    with pytest.raises(ValueError, match="terminator"):
        decode(KonamiNesRle(), cut)
    prefix, consumed, complete = decode(KonamiNesRle(), cut, partial=True)
    assert data.startswith(prefix) and not complete and consumed <= len(cut)
    # A long run splits into fills of 0x7E, never the reserved 0x7F.
    assert all(b != 0x7F for b in konami_rle.compress(b"\x55" * 300))


# -- Bit-packed text and Huffman --------------------------------------------


def test_bitpack_has_no_end_and_is_bounded() -> None:
    bp = BitPack(5)
    data = bytes([1, 2, 3, 31, 0, 17])
    ctx = PipelineContext()
    packed = bp.compress(data, ctx)
    assert len(packed) == 4
    out, consumed, complete = decode(bp, packed)
    assert out[: len(data)] == data and consumed == len(packed)
    # The packing says nothing about where a structure ends; the alphabet's own
    # terminator token does, one table stage later.
    assert complete is False
    assert len(decode(bp, b"\xff" * (MAX_OUT // 2))[0]) <= MAX_OUT


def node(left: int, right: int) -> bytes:
    """One 4-byte Huffman node: left link, right link, leaves flagged 0x8000."""
    return left.to_bytes(2, "little") + right.to_bytes(2, "little")


TREE = node(0x8000 | 0x41, 1) + node(0x8000 | 0x42, 0x8000 | 0x43)
"""root -> (A | node1), node1 -> (B | C)."""


def test_huffman_table_roundtrip() -> None:
    huff = HuffmanTable({"tree": TREE, "end_symbol": 0x43})
    ctx = PipelineContext()
    packed = huff.compress(b"ABAC", ctx)
    assert packed == bytes([0b01001100])  # 0 10 0 11, zero-padded
    out, consumed, complete = decode(huff, packed + b"\xff")
    assert out == b"ABAC" and complete and consumed == 1


def test_huffman_without_an_end_symbol_is_never_complete() -> None:
    huff = HuffmanTable({"tree": TREE})
    out, _, complete = decode(huff, bytes([0b01001100]))
    assert out.startswith(b"ABAC") and complete is False
    # With an end symbol, running out of bits before it is a truncated stream.
    ended = HuffmanTable({"tree": TREE, "end_symbol": 0x43})
    with pytest.raises(ValueError, match="no end symbol"):
        decode(ended, bytes([0b00000000]))
    out, _, complete = decode(ended, bytes([0b00000000]), partial=True)
    assert out == b"AAAAAAAA" and complete is False


def test_huffman_binds_tree_from_rom() -> None:
    rom = b"\xff" * 32 + TREE
    huff = HuffmanTable({"tree_offset": 32, "end_symbol": 0x43})
    huff.bind_tree(rom)
    assert decode(huff, bytes([0b01001100]))[0] == b"ABAC"


# -- The stage as a whole ----------------------------------------------------

ROUND_TRIP = [
    # Not the empty payload: only SLZ encodes one, the size-prefixed framings
    # rejecting a zero there so that header-shaped noise is not a structure.
    b"A",
    b"A" * 40,
    b"ABCABCABCABC HELLO HELLO HELLO WORLD" * 5,
    b"HELLO HELLO HELLO WORLD, HELLO WORLD! " * 6 + bytes(range(64)),
    bytes(300),
    bytes(random.Random(1105).randrange(256) for _ in range(2000)),
]


def test_every_registered_scheme_round_trips(registry) -> None:
    """Whatever a scheme writes, it reads back — and reads back its own extent.

    ``consumed`` may be less than the stream where a framing ends with a flag or
    descriptor byte nobody reads, so it is the content's length rather than the
    slot's; it may never claim bytes the stream does not have.
    """
    for plugin in registry.plugins(Stage.COMPRESSION):
        assert writes_back(plugin, Stage.COMPRESSION), plugin.info.id
        for data in ROUND_TRIP:
            width = getattr(plugin, "width", 0)
            if width:  # a bit packing carries symbols, not bytes
                data = bytes(b & ((1 << width) - 1) for b in data)
            ctx = PipelineContext()
            packed = plugin.compress(data, ctx)
            out, consumed, _ = decode(plugin, packed)
            assert out[: len(data)] == data, (plugin.info.id, len(data))
            assert width or out == data, (plugin.info.id, len(data))
            assert consumed <= len(packed), plugin.info.id


def test_registered(registry) -> None:
    for pid in (
        "gba_lz77",
        "bitpack6",
        "packbits",
        "lz1",
        "lz2",
        "lz2_improved",
        "kosinski",
        "prs",
        "rnc1",
        "rnc2",
        "rle1",
        "rle2",
        "konami_nes_rle",
        "konami_fds_rle",
        *PRESET_LZSS,
    ):
        assert registry.plugin(Stage.COMPRESSION, pid) is not None, pid
    # Exactly what ships, because the features doc lists it: the bit packings
    # are registered ready-made, and Huffman is the one scheme with nothing to
    # register — its node layout and tree address *are* the format, so it
    # reaches the registry only through a preset.
    assert set(registry.ids(Stage.COMPRESSION)) == {
        "gba_lz77",
        *PRESET_LZSS,
        "lz1",
        "lz1_improved",
        "lz2",
        "lz2_improved",
        "kosinski",
        "prs",
        "rnc1",
        "rnc2",
        "rle1",
        "rle2",
        "konami_nes_rle",
        "konami_fds_rle",
        "packbits",
        "bitpack5",
        "bitpack6",
        "bitpack7",
    }
    # The engine is what a preset TOML names, so its parameters are the contract.
    assert isinstance(SCHEMES["slz16"], Lzss)
    with pytest.raises(ValueError, match="window_bits"):
        Lzss({"window_bits": 13})
    with pytest.raises(ValueError, match="size_header"):
        Lzss({"size_header": "u24le"})


def test_compression_compresses(registry) -> None:
    """Every scheme is smaller than what it was given, on data it is for."""
    data = b"HELLO HELLO HELLO WORLD, HELLO WORLD! " * 6 + b"\x00" * 200
    for plugin in registry.plugins(Stage.COMPRESSION):
        if getattr(plugin, "width", 0):
            continue  # a fixed width packs by 8/width whatever the bytes are
        packed = plugin.compress(data, PipelineContext())
        assert len(packed) < len(data), plugin.info.id
