"""Konami RLE codecs — two mutually-exclusive framings of one RLE scheme.

Run-length-encoded NES/FDS data used by many Konami titles, CHR and tables
alike: the scheme says nothing about what it carries.

The core scheme is agreed: a control byte ``c`` with bit 7 clear is a **fill**
(repeat the next byte ``c`` times), bit 7 set a **literal copy** of ``c & 0x7F``
bytes, and ``0xFF`` ends the stream. But two game lineages read the reserved
``0x7F`` / ``0x80`` bytes incompatibly and no structural signal tells them apart,
so each is its own selectable scheme:

- **Contra family** (``konami_nes_rle``): ``0x7F`` is a **PPU address change** —
  the next 2 little-endian bytes reload the VRAM write cursor to place the
  following run elsewhere; ``0x80`` is an (unused) zero-length literal. The
  address is consumed but not honoured — skipping its 2 bytes is what keeps the
  stream in sync (a missed skip mis-reads the low address byte as a control and
  desyncs the tail). Fills and literals cap at ``0x7E`` because ``0x7F`` and
  ``0xFF`` are reserved.

- **Simon's Quest / FDS family** (``konami_fds_rle``): no address
  command — ``0x7F`` is a plain **127-byte fill** and ``0x80`` a **256-byte
  literal** (an incompressible block). Covers *Dracula II* / Simon's Quest, Ai
  Senshi Nicol and Rampart.

In both, the leading per-group 2-byte PPU destination is not modelled — point the
read past it.

**One shared compressor.** Both schemes encode through the same :func:`compress`,
which stays inside the *unambiguous subset* every reading decodes alike: it never
emits ``0x7F`` or ``0x80`` (runs cap at ``0x7E``), so its output round-trips under
either family's decoder. Byte-identity with a game's original blob is a non-goal;
round-tripping is the contract. The two schemes differ only in how they *decode*,
and share a base class carrying the one encoder.
"""

from __future__ import annotations

from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_BANK, stream_error
from mapchar.plugins.builtins.compression._rle import (
    Packet,
    pack_runs,
    unpack_packets,
)

_SCHEME = "Konami RLE"

# Largest byte count one fill or literal control byte can safely encode. 0x7F and
# 0xFF are reserved in the Contra reading (address change, terminator) and
# literals mask with 0x7F, so the shared compressor stays under 0x7E to keep its
# output valid for every variant.
_MAX_CHUNK = 0x7E
# Shortest run worth encoding as a fill. A fill costs 2 bytes; the same bytes left
# in a literal cost one each plus an amortised control byte, so a run only pays
# for itself at 3.
_MIN_FILL_RUN = 3


def decompress(
    data: bytes, *, fds: bool = False, partial: bool = False
) -> tuple[bytes, int, bool]:
    """Decode a Konami RLE stream.

    ``fds`` selects the Simon's Quest / FDS reading of the ``0x7F`` / ``0x80``
    control bytes (see the module docstring); the default is the Contra reading.

    Returns ``(output, consumed, complete)``. ``complete`` is true when the
    ``0xFF`` terminator was reached inside the buffer, making ``consumed`` the
    structure's true byte length — the slot a save-back must fit. Otherwise
    ``consumed`` is the end of the last complete packet, and the read raises
    unless ``partial`` says a cut-short buffer was expected. Reaching
    :data:`~mapchar.plugins.builtins.compression._limits.MAX_BANK` of output
    without the terminator means the bytes were not a stream.
    """
    out, consumed, complete = unpack_packets(
        data, header=lambda d, i: _packet(d, i, fds=fds), max_out=MAX_BANK
    )
    if not complete and not partial:
        raise stream_error(_SCHEME, "no 0xFF terminator")
    return out, consumed, complete


def _packet(data: bytes, i: int, *, fds: bool) -> tuple[Packet, int, int]:
    """One control byte, read as its family reads it."""
    c = data[i]
    if c == 0xFF:  # end (both variants)
        return Packet.END, 0, 1
    if c == 0x7F and not fds:
        # Contra: PPU address change — step over the 2-byte destination, keep going.
        return Packet.SKIP, 0, 3
    if c == 0x80 and fds:
        return Packet.LITERAL, 0x100, 1  # FDS: 256-byte incompressible literal
    if c >= 0x80:
        return Packet.LITERAL, c & 0x7F, 1  # Contra 0x80 -> 0 (unused no-op)
    # Fill: value byte repeated c times. In FDS mode 0x7F lands here as a
    # 127-fill; in Contra mode 0x7F was handled above, so c is <= 0x7E.
    return Packet.RUN, c, 2


def compress(data: bytes) -> bytes:
    """Encode raw bytes into a Konami RLE stream every variant decodes back.

    Emits a fill for every run of ``_MIN_FILL_RUN`` or more equal bytes and packs
    everything else into literal chunks, both capped at ``_MAX_CHUNK``. A run
    remainder too short for its own fill folds back into the literal buffer.
    Never emits ``0x7F`` or ``0x80``, so the output is unambiguous under both the
    Contra and FDS readings.
    """
    out = bytearray()
    pack_runs(
        data,
        out,
        literal_header=lambda count: 0x80 | count,
        run_header=lambda count: count,
        max_packet=_MAX_CHUNK,
        min_run=_MIN_FILL_RUN,
        # A 1- or 2-byte tail always ships literal, never as a fill of its own:
        # one form per byte, at the cost of the byte PackBits takes back next door.
        spill_pair_as_run=False,
    )
    out.append(0xFF)
    return bytes(out)


class _KonamiRle(PartialDecompression):
    """Shared base: the two schemes differ only in how ``_decode`` reads them.

    Encoding is the portable subset every variant accepts, so it lives here and
    each scheme supplies only its own ``fds`` flag.
    """

    fds: bool

    def _decode(self, data: bytes, *, partial: bool) -> tuple[bytes, int, bool]:
        return decompress(data, fds=self.fds, partial=partial)

    def _encode(self, data: bytes) -> bytes:
        return compress(data)


class KonamiNesRle(_KonamiRle):
    info = PluginInfo(
        "konami_nes_rle", "Konami RLE (Contra family)", Stage.COMPRESSION, "Nintendo"
    )
    fds = False


class KonamiFdsRle(_KonamiRle):
    info = PluginInfo(
        "konami_fds_rle",
        "Konami RLE (Simon's Quest / FDS family)",
        Stage.COMPRESSION,
        "Nintendo",
    )
    fds = True
