"""The run-length schemes: PackBits, the SNES RLE pair, and the two readings
of the Konami format."""

from __future__ import annotations

import pytest

from compression_helpers import decode, hexs
from mapchar.core.context import PipelineContext
from mapchar.plugins.builtins.compression import (
    KonamiFdsRle,
    KonamiNesRle,
    PackBits,
    Rle1,
    Rle2,
    konami_rle,
    snes_rle,
)
from mapchar.plugins.builtins.compression import packbits as packbits_mod
from mapchar.plugins.builtins.compression._limits import MAX_BANK, MAX_OUT

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
