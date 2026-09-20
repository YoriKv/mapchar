"""RLE1 and RLE2 — the SNES RLE, in its two framings.

The smallest scheme here after PackBits: one control byte, no back-references,
and nothing but runs and literals. Super Mario World packs its Map16 index maps
behind it, and the same two framings turn up wherever a cartridge wanted a run
encoder for two bytes a packet.

A stream is a sequence of packets, each a one-byte header ``CLLLLLLL``:

| header | meaning |
|---|---|
| ``0x00``–``0x7F`` (C=0) | **literal**: copy the next ``L + 1`` bytes verbatim |
| ``0x80``–``0xFF`` (C=1) | **run**: repeat the next byte ``L + 1`` times |

Both kinds carry 1–128 output bytes, so a run always pays for itself from two
bytes up: two bytes of packet against the two the same bytes cost inside a
literal, plus the literal's amortised header.

**The two framings differ only in where the stream ends**, and that difference is
the whole of the split between the two plugins here:

- **RLE1** ends on ``$FF $FF`` *at a header position* — a header that says "run of
  128" followed by a value that is also ``$FF``. The stream finds its own extent,
  so a block needs no length.
- **RLE2** has no end marker at all. Its decoder stops on an output byte count it
  already knows (the size of the buffer it is filling), so it never reports a
  complete structure and an RLE2 block carries an explicit length.

**128 copies of ``$FF`` cannot be encoded**, because the header ``$FF`` and the
value ``$FF`` spell the terminator. :func:`compress` caps a run of that one byte
at 127 under the terminated framing and lets the remainder ride along as a short
run or a literal. Under RLE2 there is no terminator and no cap.

**Compressor.** A run for every stretch of two or more equal bytes, everything
else packed into literals, both capped at 128. That threshold is the original
packer's rather than the optimal one, which is what keeps an unedited structure
byte-identical and so certain to still fit its slot.
"""

from __future__ import annotations

from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_BANK, stream_error
from mapchar.plugins.builtins.compression._rle import Packet, pack_runs, unpack_packets

_SCHEME = "RLE1"  # only the terminated framing can refuse a stream at all

# Output bytes one packet can carry, literal or run: (L + 1) over a 7-bit L.
_MAX_PACKET = 128
# Shortest run worth its own packet — the original packer's threshold; see the
# module docstring on why it is 2 and not 3.
_MIN_RUN = 2
# The terminated framing's end marker: a full 128-run of $FF, read as an end
# rather than as a packet, which is exactly why the encoder may not write one.
_TERMINATOR = b"\xff\xff"
_MAX_RUN_OF_TERMINATOR_BYTE = 127


def decompress(
    data: bytes, *, terminated: bool, partial: bool = False
) -> tuple[bytes, int, bool]:
    """Decode one stream at ``data[0]``; returns ``(output, consumed, complete)``.

    ``terminated`` selects the RLE1 framing: stop at ``$FF $FF`` and report the
    structure's true extent. Without it (RLE2) there is no end to find — the
    decode runs until the buffer or the output cap is exhausted and ``complete``
    is always false, since "the buffer ran out" is not "the structure ended" and
    claiming otherwise would let a block created without a length backfill its
    extent as the whole rest of the file.

    Under ``terminated``, a buffer that ends before the terminator raises unless
    ``partial`` is set — a bounded view window routinely cuts a stream short, and
    the prefix it did decode is what the view wants to show.

    The read is bounded by one bank of output
    (:data:`~mapchar.plugins.builtins.compression._limits.MAX_BANK`), which for
    RLE1 then reads as "no terminator": a stream that expands that far without
    ending is not one.

    ``consumed`` counts through the terminator for a complete RLE1 read, making it
    the structure's true length — the slot a save-back must fit. Otherwise it is
    the end of the last *whole* packet, the only boundary a cut-short buffer
    offers; a half-delivered literal still contributes the bytes that did arrive.
    """
    out, consumed, complete = unpack_packets(
        data, header=lambda d, i: _packet(d, i, terminated=terminated), max_out=MAX_BANK
    )
    if terminated and not complete and not partial:
        raise stream_error(_SCHEME, "no $FF $FF terminator")
    return out, consumed, complete


def _packet(data: bytes, i: int, *, terminated: bool) -> tuple[Packet, int, int]:
    """One ``CLLLLLLL`` header, or the terminator standing where one would be."""
    if terminated and data[i : i + 2] == _TERMINATOR:
        return Packet.END, 0, 2
    header = data[i]
    if header & 0x80:  # run: the next byte, (L + 1) times
        return Packet.RUN, (header & 0x7F) + 1, 2
    return Packet.LITERAL, header + 1, 1  # literal: the next (L + 1) bytes


def _run_limit(value: int) -> int:
    """How long a run of ``value`` may be under the terminated framing.

    128 copies of ``$FF`` write the header ``$FF`` and the value ``$FF`` — the end
    marker — so that one byte stops a packet short and the remainder rides along
    behind it. Every other byte fills the packet.
    """
    return _MAX_RUN_OF_TERMINATOR_BYTE if value == 0xFF else _MAX_PACKET


def compress(data: bytes, *, terminated: bool) -> bytes:
    """Encode raw bytes as an RLE1 (``terminated``) or RLE2 stream."""
    out = bytearray()
    pack_runs(
        data,
        out,
        literal_header=lambda count: count - 1,
        run_header=lambda count: 0x80 | (count - 1),
        max_packet=_MAX_PACKET,
        min_run=_MIN_RUN,
        # A pair is already worth its own run packet here (_MIN_RUN is 2), so the
        # trade PackBits makes for one never arises.
        spill_pair_as_run=False,
        # RLE2 has no terminator for a run to collide with, so no cap.
        run_limit=_run_limit if terminated else None,
    )
    if terminated:
        out += _TERMINATOR
    return bytes(out)


class _SnesRle(PartialDecompression):
    """Shared base: the two plugins differ only in ``_terminated``."""

    _terminated: bool

    def _decode(self, data: bytes, *, partial: bool) -> tuple[bytes, int, bool]:
        return decompress(data, terminated=self._terminated, partial=partial)

    def _encode(self, data: bytes) -> bytes:
        return compress(data, terminated=self._terminated)


class Rle1(_SnesRle):
    info = PluginInfo(
        "rle1", "RLE1 (SMW, $FF $FF terminated)", Stage.COMPRESSION, "Nintendo"
    )
    _terminated = True


class Rle2(_SnesRle):
    # No end marker: the extent comes from the block, as for PackBits.
    info = PluginInfo(
        "rle2", "RLE2 (SMW, no terminator)", Stage.COMPRESSION, "Nintendo"
    )
    _terminated = False
