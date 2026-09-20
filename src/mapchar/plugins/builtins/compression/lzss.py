"""The LZSS family: one engine, one preset per framing.

Every scheme here is flag-group LZSS — eight op selectors in a byte, then a
literal or a two-byte back-reference each — so they share a decoder and a
compressor, and a preset says only how the pieces are laid out. What differs
between real formats is small and entirely silent when read the wrong way round:
which end of the flags byte the first selector sits at, whether a set bit picks
the reference or the literal, which nibble of the reference holds the length,
what the distance field is biased by, and how the decompressed size is known.

Parameters, all optional:

``window_bits`` (12) / ``length_bits`` (4)
    Field widths inside the two-byte reference; they must add to 16.
``min_match`` (3)
    What the length field is biased by, so lengths run ``min_match`` to
    ``min_match + 2**length_bits - 1``.
``ref_format``
    How the two bytes ``b0 b1`` carry the two fields:

    - ``gba``: ``b0 = length << 4 | offset >> 8``, ``b1 = offset & FF``;
    - ``offset_length``: ``b0 = offset >> 4``, ``b1 = (offset & F) << 4 | length``;
    - ``okumura``: ``b0 = offset & FF``, ``b1 = (offset >> 8) << 4 | length``.
``flags_msb_first`` (True) / ``set_is_ref`` (True)
    Which bit of a flags byte is the first op's, and what a set bit selects.
``offset_kind``
    ``distance`` reads the field as ``distance - distance_bias`` back from the
    write position; ``ring`` reads it as an absolute position in a ring buffer of
    ``2**window_bits`` bytes, filled with ``ring_init`` and written from
    ``ring_start``.
``distance_bias`` (1) / ``min_distance``
    What a stored distance is biased by, and the nearest distance the compressor
    will *write* — by default the bias, since a biased field cannot name anything
    nearer. A preset raises it where the format can encode a distance its
    consumer rejects (the VRAM-safe BIOS call and stored 0).
``size_header``
    How the decompressed size is known: ``gba`` (``1S SS SS SS``), ``u16le``,
    ``u32le``, ``u16be``, ``u24be``, or ``none``. The size is the only
    terminator these framings have, so ``none`` can never report a complete
    structure.
``magic`` / ``magic_mask`` (FF)
    A first byte the header must carry, under that mask.
``allow_zero_size`` (False)
    Whether a declared size of zero is the format's empty payload rather than
    noise that happened to land where a header was expected.
``clip_last_match`` (True)
    Whether a final reference may overrun the declared size and be cut short, as
    the BIOS decoders do, or whether overshooting means the stream is not this
    format's (SLZ lands on the size exactly).
"""

from __future__ import annotations

from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_OUT, stream_error
from mapchar.plugins.builtins.compression._lz import (
    FlagGroup,
    MatchFinder,
    copy_from,
    parse_greedy,
)

_HEADER_SIZES = {"none": 0, "gba": 4, "u16le": 2, "u32le": 4, "u16be": 2, "u24be": 3}


class Lzss(PartialDecompression):
    """A parameterised LZSS framing; see the module docstring for the parameters."""

    def __init__(
        self,
        params: dict,
        id: str = "lzss",
        name: str = "LZSS",
        category: str = "Generic",
    ):
        self.window_bits = int(params.get("window_bits", 12))
        self.length_bits = int(params.get("length_bits", 4))
        self.min_match = int(params.get("min_match", 3))
        self.flags_msb_first = bool(params.get("flags_msb_first", True))
        self.set_is_ref = bool(params.get("set_is_ref", True))
        self.ref_format = str(params.get("ref_format", "gba"))
        self.offset_kind = str(params.get("offset_kind", "distance"))
        self.distance_bias = int(params.get("distance_bias", 1))
        self.min_distance = int(
            params.get("min_distance", max(1, self.distance_bias)),
        )
        self.ring_init = int(params.get("ring_init", 0))
        self.ring_start = int(params.get("ring_start", 0))
        self.size_header = str(params.get("size_header", "none"))
        self.magic = params.get("magic")
        self.magic_mask = int(params.get("magic_mask", 0xFF))
        self.allow_zero_size = bool(params.get("allow_zero_size", False))
        self.clip_last_match = bool(params.get("clip_last_match", True))
        self.info = PluginInfo(id, name, Stage.COMPRESSION, category)
        if self.window_bits + self.length_bits != 16:
            raise ValueError("window_bits + length_bits must be 16")
        if self.size_header not in _HEADER_SIZES:
            raise ValueError(f"unknown size_header {self.size_header!r}")

    @property
    def max_match(self) -> int:
        return (1 << self.length_bits) - 1 + self.min_match

    @property
    def ring_size(self) -> int:
        return 1 << self.window_bits

    @property
    def max_distance(self) -> int:
        """The farthest back a reference reaches — the compressor's window."""
        if self.offset_kind == "ring":
            return self.ring_size
        return (1 << self.window_bits) - 1 + self.distance_bias

    @property
    def header_size(self) -> int:
        return _HEADER_SIZES[self.size_header]

    def _error(self, reason: str) -> ValueError:
        """:func:`stream_error` under this framing's **display name**.

        The name and not the id, because the message is what the UI shows
        against the entry: every other scheme here names itself the way the
        picker does, and a preset's id is not what the picker called it.
        """
        return stream_error(self.info.name, reason)

    def _read_size(self, data: bytes) -> int | None:
        if self.size_header == "gba":
            return int.from_bytes(data[1:4], "little")
        if self.size_header == "u16le":
            return int.from_bytes(data[0:2], "little")
        if self.size_header == "u32le":
            return int.from_bytes(data[0:4], "little")
        if self.size_header == "u16be":
            return int.from_bytes(data[0:2], "big")
        if self.size_header == "u24be":
            return int.from_bytes(data[0:3], "big")
        return None

    def _write_size(self, n: int) -> bytes:
        if self.size_header == "gba":
            first = self.magic if self.magic is not None else 0x10
            return bytes([first]) + n.to_bytes(3, "little")
        if self.size_header == "u16le":
            return n.to_bytes(2, "little")
        if self.size_header == "u32le":
            return n.to_bytes(4, "little")
        if self.size_header == "u16be":
            return n.to_bytes(2, "big")
        if self.size_header == "u24be":
            return n.to_bytes(3, "big")
        return b""

    @property
    def max_size(self) -> int | None:
        """The largest payload the size header can state, or ``None`` for no header."""
        if self.size_header == "none":
            return None
        if self.size_header == "gba":
            return 0xFFFFFF
        return (1 << (self.header_size * 8)) - 1

    def _unpack_ref(self, b0: int, b1: int) -> tuple[int, int]:
        """``(field, length)``: the offset field as stored, and the real length."""
        lmask = (1 << self.length_bits) - 1
        if self.ref_format == "gba":
            word = (b0 << 8) | b1
            length = word >> self.window_bits
            offset = word & ((1 << self.window_bits) - 1)
        elif self.ref_format == "okumura":
            offset = b0 | ((b1 >> self.length_bits) << 8)
            length = b1 & lmask
        else:  # offset_length
            word = (b0 << 8) | b1
            offset = word >> self.length_bits
            length = word & lmask
        return offset, length + self.min_match

    def _pack_ref(self, offset: int, length: int) -> bytes:
        length -= self.min_match
        if self.ref_format == "gba":
            word = (length << self.window_bits) | offset
        elif self.ref_format == "okumura":
            return bytes([offset & 0xFF, ((offset >> 8) << self.length_bits) | length])
        else:
            word = (offset << self.length_bits) | length
        return word.to_bytes(2, "big")

    def _decode(self, data: bytes, *, partial: bool) -> tuple[bytes, int, bool]:
        head = self.header_size
        if len(data) < head:
            raise self._error(f"shorter than the {head}-byte header")
        if self.magic is not None and data[0] & self.magic_mask != self.magic:
            raise self._error(
                f"byte {data[0]:#04x} is not a {self.magic:#04x} header"
                f" (mask {self.magic_mask:#04x})"
            )
        target = self._read_size(data)
        if target == 0:
            if self.allow_zero_size:
                # The format's own encoding of an empty payload.
                return b"", head, True
            # Accepting it would make a run of header-shaped noise a structure.
            raise self._error("declared decompressed size is zero")
        over_cap = target is not None and target > MAX_OUT
        if over_cap and not partial:
            raise self._error(
                f"declares {target:,} bytes, past the {MAX_OUT:,}-byte cap"
            )
        limit = MAX_OUT if target is None else min(target, MAX_OUT)

        # `win` is the output behind the ring's fill, so a reference reaching back
        # before the first output byte reads the fill exactly as the ring would.
        # Output byte i is win[prefix + i], and a distance scheme has no prefix.
        prefix = self.ring_size if self.offset_kind == "ring" else 0
        win = bytearray([self.ring_init]) * prefix
        src = head
        n = len(data)
        consumed = src  # the end of the last complete op, never past the buffer
        short = False
        while len(win) - prefix < limit and src < n and not short:
            flags = data[src]
            src += 1
            for bit in range(8):
                produced = len(win) - prefix
                if produced >= limit:
                    break
                if self.flags_msb_first:
                    one = (flags >> (7 - bit)) & 1
                else:
                    one = (flags >> bit) & 1
                if bool(one) != self.set_is_ref:
                    if src >= n:
                        short = True
                        break
                    win.append(data[src])
                    src += 1
                    consumed = src
                    continue
                if src + 1 >= n:
                    short = True
                    break
                field, length = self._unpack_ref(data[src], data[src + 1])
                src += 2
                if self.clip_last_match:
                    # The size is the only terminator, so a final reference is
                    # routinely cut off part way through.
                    length = min(length, limit - produced)
                if self.offset_kind == "ring":
                    # The absolute ring position names the one output position in
                    # the last ring_size bytes congruent to it modulo the ring.
                    base = produced - self.ring_size
                    start = (
                        prefix
                        + base
                        + ((field - self.ring_start - base) % self.ring_size)
                    )
                else:
                    distance = field + self.distance_bias
                    start = len(win) - distance
                    if start < 0:
                        raise self._error(
                            f"back reference at output byte {produced:,} reaches "
                            f"{-start} bytes before the start of the data"
                        )
                copy_from(win, start, length)
                consumed = src

        out = bytes(win[prefix:])
        if target is not None and len(out) > target:
            # Not a match to clip: a framing that lands on its size exactly says
            # the flags were not the ones this stream was written with.
            raise self._error(
                f"produced {len(out):,} bytes against a declared {target:,}"
            )
        if target is None:
            # No end marker and no size: where the buffer stopped is not where a
            # structure ended, and a short read is not an error either.
            return out, consumed, False
        complete = not over_cap and len(out) == target
        if not complete and not partial:
            raise self._error(f"source ended after {len(out):,} of {target:,} bytes")
        return out, consumed, complete

    def _encode(self, data: bytes) -> bytes:
        n = len(data)
        limit = self.max_size
        if limit is not None and n > limit:
            raise ValueError(
                f"input is {n:,} bytes; the {self.info.id} size field holds {limit:,}"
            )
        out = bytearray(self._write_size(n))
        if not n:
            return bytes(out)
        finder = MatchFinder(data, min_match=self.min_match, window=self.max_distance)
        group = FlagGroup(
            out, msb_first=self.flags_msb_first, set_means_match=self.set_is_ref
        )
        ring_mask = self.ring_size - 1
        for pos, length, candidate in parse_greedy(
            data,
            finder,
            min_match=self.min_match,
            max_match=self.max_match,
            min_distance=self.min_distance,
        ):
            group.select(length > 0)
            if not length:
                out.append(data[pos])
                continue
            if self.offset_kind == "ring":
                field = (self.ring_start + candidate) & ring_mask
            else:
                field = pos - candidate - self.distance_bias
            out += self._pack_ref(field, length)
        return bytes(out)


GBA_LZ77 = {
    "size_header": "gba",
    "magic": 0x10,
    # The low nibble is reserved and ignored by hardware, so it is masked off
    # rather than required to be zero: real ROM data carries stray values there.
    "magic_mask": 0xF0,
    "ref_format": "gba",
    "offset_kind": "distance",
    "distance_bias": 1,
    # Distance 1 is a stored displacement of 0, which the VRAM-safe BIOS call
    # (SWI 0x12) rejects and the byte-writing one (SWI 0x11) accepts. Writing
    # from 2 up costs one literal at the head of a run and makes one stream valid
    # for both calls.
    "min_distance": 2,
}
"""GBA/NDS BIOS LZ77: ``10 SS SS SS``, MSB-first flags, a set bit is a reference.

The stream is not padded. The BIOS reads it a byte at a time, and padding would
only inflate the slot a save-back has to fit.
"""


class GbaLz77(Lzss):
    """GBA/NDS BIOS LZ77: ``10 SS SS SS`` header, 8-flag groups, 12-bit back
    references of 3..18 bytes."""

    def __init__(self) -> None:
        super().__init__(GBA_LZ77, "gba_lz77", "GBA BIOS LZ77", "Nintendo")


PRESET_LZSS: dict[str, tuple[str, dict]] = {
    "lzss_ring": (
        "LZSS (4 KiB ring, u32 size)",
        {
            # The most widely copied LZSS framing in console software: a u32le
            # size prefix, a zero-filled 4 KiB ring written from 0xFEE, LSB-first
            # flags, and a set bit meaning the literal — every one of those the
            # opposite way round from the BIOS LZ77 above.
            "size_header": "u32le",
            "flags_msb_first": False,
            "set_is_ref": False,
            "ref_format": "okumura",
            "offset_kind": "ring",
            "ring_init": 0,
            "ring_start": 0xFEE,
        },
    ),
    "lzss_classic": (
        "LZSS (ring buffer, Okumura)",
        {
            # The reference publication's own framing: the same ring, filled with
            # spaces and with no size prefix, so the extent comes from outside.
            "flags_msb_first": False,
            "set_is_ref": False,
            "ref_format": "okumura",
            "offset_kind": "ring",
            "ring_init": 0x20,
            "ring_start": 0xFEE,
        },
    ),
    "slz16": (
        "SLZ16 (Mega Drive, 64 KB payload)",
        {
            "category": "Sega",
            # Big-endian size, MSB-first flags, and a reference whose high twelve
            # bits are the distance biased by 3 — the nibble roles the other way
            # round from the BIOS LZ77, which is silent when read as one another.
            "size_header": "u16be",
            "ref_format": "offset_length",
            "distance_bias": 3,
            "allow_zero_size": True,
            "clip_last_match": False,
        },
    ),
    "slz24": (
        "SLZ24 (Mega Drive, 16 MB payload)",
        {
            "category": "Sega",
            "size_header": "u24be",
            "ref_format": "offset_length",
            "distance_bias": 3,
            "allow_zero_size": True,
            "clip_last_match": False,
        },
    ),
}
"""The framings that ship as presets of :class:`Lzss`, by plugin id.

``category`` beside the engine's own parameters is
:attr:`~mapchar.plugins.base.PluginInfo.category` and not a parameter at all:
:func:`presets` takes it off and passes it to the ``PluginInfo`` instead.
"""


def presets() -> list[Lzss]:
    """Every LZSS framing that ships, in the order the pickers list them."""
    out = [GbaLz77()]
    for pid, (name, params) in PRESET_LZSS.items():
        fields = dict(params)
        out.append(Lzss(fields, pid, name, str(fields.pop("category", "Generic"))))
    return out
