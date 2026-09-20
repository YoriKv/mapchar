"""RNC methods 1 and 2: the header the decoder verifies, the two bit readings,
and the extent a stream declares."""

from __future__ import annotations

import random

import pytest

from compression_helpers import decode, hexs
from conftest import ROOT
from mapchar.plugins.builtins.compression import Rnc1, Rnc2, rnc
from mapchar.plugins.builtins.compression._limits import MAX_OUT


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
