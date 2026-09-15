"""PackBits — Apple's byte-oriented RLE, as used by TIFF, IFF/ILBM and MacPaint.

A stream of packets, each a one-byte control read as a *signed* value:

| control ``c``                | meaning                                       |
|------------------------------|-----------------------------------------------|
| ``00``-``7F`` (0..127)       | literal: copy the next ``c + 1`` bytes         |
| ``81``-``FF`` (-127..-1)     | run: repeat the next byte ``257 - c`` times    |
| ``80`` (-128)                | no-op: skip, the next byte is another control  |

**There is no terminator and no length field**, and *any* byte sequence decodes
as valid PackBits. So ``KEY_COMPLETE`` is never reported — a decode that runs to
the end of the buffer means the buffer ran out, not that the structure ended —
and a Scan cannot find PackBits streams, its criterion being a complete decode.
Give a PackBits block an explicit length.

:data:`~mapchar.plugins.builtins.compression._limits.MAX_OUT` bounds that
openness in memory, an unbounded read otherwise expanding to 128x the file's
tail. Hitting it stops the decode at the last packet boundary rather than
raising: a PackBits stream has no end to be inconsistent with.

The encoder emits a run for every stretch of 3 or more equal bytes and packs
everything else into literals, both capped at 128 bytes. Round-tripping is the
contract, not byte-identity with another packer.
"""

from __future__ import annotations

from mapchar.core.context import KEY_COMPLETE, KEY_CONSUMED, PipelineContext
from mapchar.plugins.base import PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_OUT
from mapchar.plugins.builtins.compression._rle import (
    Packet,
    pack_runs,
    unpack_packets,
)

_MAX_PACKET = 128
_NOP = 0x80
# Shortest run worth its own packet. A run costs 2 bytes; the same bytes appended
# to an *open* literal packet cost one each with no new control byte, so a 2-run
# only pays for itself when no literal packet is open to absorb it.
_MIN_RUN = 3


def decompress(data: bytes) -> tuple[bytes, int]:
    """Decode a PackBits stream; returns ``(output, consumed)``.

    ``consumed`` is the end of the last *complete* packet — the offset a
    following structure could start at, not the structure's true extent, which
    PackBits has no terminator to find. A truncated literal still contributes
    the bytes that are present, a bounded window routinely slicing mid-packet.
    """
    out, consumed, _complete = unpack_packets(data, header=_packet, max_out=MAX_OUT)
    return out, consumed


def _packet(data: bytes, i: int) -> tuple[Packet, int, int]:
    """One control byte, read as the signed count PackBits writes."""
    control = data[i]
    if control == _NOP:
        return Packet.SKIP, 0, 1
    if control < _NOP:
        return Packet.LITERAL, control + 1, 1
    return Packet.RUN, 257 - control, 2


def compress(data: bytes) -> bytes:
    """Encode raw bytes as a PackBits stream.

    The control byte is a *signed* count: a literal states one less than the
    bytes that follow it, and a run states ``257 - count``, that count's
    negation read back as an unsigned byte.
    """
    out = bytearray()
    pack_runs(
        data,
        out,
        literal_header=lambda count: count - 1,
        run_header=lambda count: 257 - count,
        max_packet=_MAX_PACKET,
        min_run=_MIN_RUN,
        # A lone pair with no literal packet open is cheaper as a run.
        spill_pair_as_run=True,
    )
    return bytes(out)


class PackBits:
    info = PluginInfo("packbits", "PackBits RLE", Stage.COMPRESSION, "Generic")

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        out, consumed = decompress(data)
        ctx.set(KEY_CONSUMED, consumed)
        # Never complete: with no end marker, "we decoded to here" is not "the
        # structure ends here".
        ctx.set(KEY_COMPLETE, False)
        return out

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        return compress(data)
