"""Built-in text compression schemes.

Each decompresses from the start of its input, publishes how many bytes it
consumed (``KEY_CONSUMED``) and whether it saw its end (``KEY_COMPLETE``),
and compresses back where the scheme allows.
"""

from __future__ import annotations

from mapchar.core.bits import bits_to_bytes, bytes_to_bits, reverse_bits
from mapchar.core.context import KEY_COMPLETE, KEY_CONSUMED, PipelineContext
from mapchar.plugins.base import PluginInfo, Stage


class BitPack:
    """Fixed-width symbols packed MSB-first; one byte per symbol unpacked."""

    def __init__(self, width: int):
        self.width = width
        self.info = PluginInfo(
            f"bitpack{width}",
            f"{width}-bit packed symbols",
            Stage.COMPRESSION,
            "Generic",
        )

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        bits = bytes_to_bits(data)
        n = len(bits) // self.width
        out = bytes(
            int(bits[i * self.width : (i + 1) * self.width], 2) for i in range(n)
        )
        ctx.set(KEY_CONSUMED, len(data))
        ctx.set(KEY_COMPLETE, True)
        return out

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        bits = "".join(
            format(b & ((1 << self.width) - 1), f"0{self.width}b") for b in data
        )
        return bits_to_bytes(bits)


class HuffmanTable:
    """Huffman text through a node table stored in the ROM.

    Parameters name where the tree lives and how a node is laid out: nodes
    are ``node_size`` bytes, holding a left and a right link at byte offsets
    ``left``/``right`` (little-endian, ``link_size`` bytes). A link with the
    ``leaf_flag`` bit set is a leaf whose symbol is the link masked by
    ``leaf_mask``; otherwise it indexes another node. Decoding starts at
    ``root`` and reads bits MSB-first (``lsb_first`` flips that), one symbol
    per leaf, until the ``end_symbol`` or the data runs out. Compression
    walks the same tree backwards.
    """

    def __init__(
        self, params: dict, id: str = "huffman", name: str = "Huffman (node table)"
    ):
        self.tree = bytes(params.get("tree", b""))
        self.tree_offset = int(params.get("tree_offset", 0))
        self.node_size = int(params.get("node_size", 4))
        self.left = int(params.get("left", 0))
        self.right = int(params.get("right", 2))
        self.link_size = int(params.get("link_size", 2))
        self.leaf_flag = int(params.get("leaf_flag", 0x8000))
        self.leaf_mask = int(params.get("leaf_mask", 0xFF))
        self.root = int(params.get("root", 0))
        self.lsb_first = bool(params.get("lsb_first", False))
        self.end_symbol = params.get("end_symbol")
        self.info = PluginInfo(id, name, Stage.COMPRESSION, "Generic")
        self._paths: dict[int, str] | None = None

    def bind_tree(self, rom: bytes) -> None:
        """Take the tree bytes from the ROM at ``tree_offset`` when none were given."""
        if not self.tree:
            self.tree = rom[self.tree_offset :]

    def _link(self, node: int, side: int) -> int:
        at = node * self.node_size + side
        return int.from_bytes(self.tree[at : at + self.link_size], "little")

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        out = bytearray()
        node = self.root
        total_bits = len(data) * 8
        consumed_bits = 0
        complete = False
        for i in range(total_bits):
            byte = data[i // 8]
            bit = (byte >> (i % 8)) & 1 if self.lsb_first else (byte >> (7 - i % 8)) & 1
            link = self._link(node, self.right if bit else self.left)
            if link & self.leaf_flag:
                symbol = link & self.leaf_mask
                out.append(symbol)
                consumed_bits = i + 1
                node = self.root
                if self.end_symbol is not None and symbol == int(self.end_symbol):
                    complete = True
                    break
            else:
                node = link
        ctx.set(KEY_CONSUMED, -(-consumed_bits // 8))
        ctx.set(KEY_COMPLETE, complete)
        return bytes(out)

    def _build_paths(self) -> dict[int, str]:
        paths: dict[int, str] = {}
        stack = [(self.root, "")]
        seen = set()
        while stack:
            node, path = stack.pop()
            if node in seen or len(path) > 64:
                continue
            seen.add(node)
            for side, bit in ((self.left, "0"), (self.right, "1")):
                link = self._link(node, side)
                if link & self.leaf_flag:
                    paths.setdefault(link & self.leaf_mask, path + bit)
                else:
                    stack.append((link, path + bit))
        return paths

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        if self._paths is None:
            self._paths = self._build_paths()
        bits = []
        for symbol in data:
            path = self._paths.get(symbol)
            if path is None:
                raise ValueError(f"symbol {symbol:02X} is not in the tree")
            bits.append(path)
        out = bits_to_bytes("".join(bits))
        return reverse_bits(out) if self.lsb_first else out


class Lzss:
    """A parameterised LZSS family.

    A reference copies ``length + min_match`` bytes from ``offset``, packed
    in one of these ``ref_format`` layouts (two bytes ``b0 b1``):

    - ``gba``: ``b0 = length << 4 | offset >> 8``, ``b1 = offset & FF``;
    - ``offset_length``: ``b0 = offset >> 4``, ``b1 = (offset & F) << 4 | length``;
    - ``okumura``: ``b0 = offset & FF``, ``b1 = (offset >> 8) << 4 | length``.

    ``window_bits`` (12) and ``length_bits`` (4) fix the field widths. Flags
    come in groups of eight, read from the most significant bit
    (``flags_msb_first``) or the least, and a set bit means a reference when
    ``set_is_ref``. Offsets are ``distance`` (back from the write position,
    plus one) or ``ring`` (an absolute position in a ring buffer of
    ``2**window_bits`` bytes, initialised to ``ring_init`` and written from
    ``ring_start``). A ``size_header`` of ``gba`` (``10 SS SS SS``),
    ``u16le``, ``u32le`` or ``none`` says how the decompressed size is known;
    ``magic`` is a first byte the header must carry, and ``pad_to`` a multiple
    the compressed output is zero-padded to.
    """

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
        self.ring_init = int(params.get("ring_init", 0))
        self.ring_start = int(params.get("ring_start", 0))
        self.size_header = str(params.get("size_header", "none"))
        self.magic = params.get("magic")
        self.pad_to = int(params.get("pad_to", 0))
        self.info = PluginInfo(id, name, Stage.COMPRESSION, category)
        if self.window_bits + self.length_bits != 16:
            raise ValueError("window_bits + length_bits must be 16")

    @property
    def max_match(self) -> int:
        return (1 << self.length_bits) - 1 + self.min_match

    @property
    def ring_size(self) -> int:
        return 1 << self.window_bits

    def _read_size(self, data: bytes) -> tuple[int | None, int]:
        if self.size_header == "gba":
            return int.from_bytes(data[1:4], "little"), 4
        if self.size_header == "u16le":
            return int.from_bytes(data[0:2], "little"), 2
        if self.size_header == "u32le":
            return int.from_bytes(data[0:4], "little"), 4
        return None, 0

    def _write_size(self, n: int) -> bytes:
        if self.size_header == "gba":
            return b"\x10" + n.to_bytes(3, "little")
        if self.size_header == "u16le":
            return n.to_bytes(2, "little")
        if self.size_header == "u32le":
            return n.to_bytes(4, "little")
        return b""

    def _unpack_ref(self, b0: int, b1: int) -> tuple[int, int]:
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

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        size, pos = self._read_size(data)
        if self.magic is not None and (len(data) < pos or data[0] != self.magic):
            raise ValueError(f"not an LZ77 stream (no {self.magic:02X} header)")
        out = bytearray()
        ring = bytearray([self.ring_init]) * self.ring_size
        ring_pos = self.ring_start
        complete = size is None
        while pos < len(data) and (size is None or len(out) < size):
            flags = data[pos]
            pos += 1
            for i in range(8):
                if size is not None and len(out) >= size:
                    break
                if pos >= len(data):
                    break
                bit = (
                    (flags >> (7 - i)) & 1 if self.flags_msb_first else (flags >> i) & 1
                )
                is_ref = bool(bit) == self.set_is_ref
                if not is_ref:
                    b = data[pos]
                    pos += 1
                    out.append(b)
                    ring[ring_pos] = b
                    ring_pos = (ring_pos + 1) % self.ring_size
                    continue
                if pos + 2 > len(data):
                    pos = len(data)
                    break
                offset, length = self._unpack_ref(data[pos], data[pos + 1])
                pos += 2
                for k in range(length):
                    if self.offset_kind == "ring":
                        b = ring[(offset + k) % self.ring_size]
                    else:
                        back = offset + 1
                        if back > len(out):
                            raise ValueError("back reference before the start")
                        b = out[-back]
                    out.append(b)
                    ring[ring_pos] = b
                    ring_pos = (ring_pos + 1) % self.ring_size
        if size is not None:
            complete = len(out) >= size
            out = out[:size]
        ctx.set(KEY_CONSUMED, pos)
        ctx.set(KEY_COMPLETE, complete)
        return bytes(out)

    def _best_ring_match(self, data: bytes, pos: int, ring: bytearray, ring_pos: int):
        """The longest match readable from the ring as the decoder will see it."""
        n = len(data)
        size = self.ring_size
        best_len, best_off = 0, 0
        for start in range(size):
            length = 0
            while length < self.max_match and pos + length < n:
                idx = start + length
                d = (idx - ring_pos) % size
                # Bytes this reference has already produced overwrite the ring.
                have = data[pos + d] if d < length else ring[idx % size]
                if have != data[pos + length]:
                    break
                length += 1
            if length > best_len:
                best_len, best_off = length, start
                if length == self.max_match:
                    break
        return best_len, best_off

    def _best_distance_match(self, data: bytes, pos: int):
        n = len(data)
        best_len, best_off = 0, 0
        for start in range(max(0, pos - self.ring_size), pos):
            length = 0
            while (
                length < self.max_match
                and pos + length < n
                and data[start + length] == data[pos + length]
            ):
                length += 1
            if length > best_len:
                best_len, best_off = length, pos - start - 1
                if length == self.max_match:
                    break
        return best_len, best_off

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        out = bytearray(self._write_size(len(data)))
        n = len(data)
        pos = 0
        ring = bytearray([self.ring_init]) * self.ring_size
        ring_pos = self.ring_start
        while pos < n:
            flags = 0
            group = bytearray()
            for i in range(8):
                if pos >= n:
                    break
                if self.offset_kind == "ring":
                    best_len, best_off = self._best_ring_match(
                        data, pos, ring, ring_pos
                    )
                else:
                    best_len, best_off = self._best_distance_match(data, pos)
                if best_len >= self.min_match:
                    bit = 1 if self.set_is_ref else 0
                    group += self._pack_ref(best_off, best_len)
                    count = best_len
                else:
                    bit = 0 if self.set_is_ref else 1
                    group.append(data[pos])
                    count = 1
                if bit:
                    flags |= (0x80 >> i) if self.flags_msb_first else (1 << i)
                for k in range(count):
                    ring[ring_pos] = data[pos + k]
                    ring_pos = (ring_pos + 1) % self.ring_size
                pos += count
            out.append(flags)
            out += group
        while self.pad_to > 1 and len(out) % self.pad_to:
            out.append(0)
        return bytes(out)


GBA_LZ77 = {
    "size_header": "gba",
    "magic": 0x10,
    "pad_to": 4,
    "window_bits": 12,
    "length_bits": 4,
    "min_match": 3,
    "flags_msb_first": True,
    "set_is_ref": True,
    "ref_format": "gba",
    "offset_kind": "distance",
}


class GbaLz77(Lzss):
    """GBA/NDS BIOS LZ77: ``10 SS SS SS`` header, 8-flag groups, 12-bit back
    references of 3..18 bytes."""

    def __init__(self) -> None:
        super().__init__(GBA_LZ77, "gba_lz77", "GBA BIOS LZ77", "Nintendo")


class PackBits:
    """Apple PackBits run-length coding, to the end of the input."""

    info = PluginInfo("packbits", "PackBits RLE", Stage.COMPRESSION, "Generic")

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        out = bytearray()
        pos = 0
        while pos < len(data):
            n = data[pos]
            pos += 1
            if n == 128:
                continue
            if n < 128:
                out += data[pos : pos + n + 1]
                pos += n + 1
            else:
                if pos >= len(data):
                    break
                out += bytes([data[pos]]) * (257 - n)
                pos += 1
        ctx.set(KEY_CONSUMED, pos)
        ctx.set(KEY_COMPLETE, True)
        return bytes(out)

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        out = bytearray()
        pos = 0
        n = len(data)
        while pos < n:
            run = 1
            while pos + run < n and run < 128 and data[pos + run] == data[pos]:
                run += 1
            if run >= 2:
                out += bytes([257 - run, data[pos]])
                pos += run
                continue
            start = pos
            while (
                pos < n
                and pos - start < 128
                and not (
                    pos + 1 < n
                    and data[pos + 1] == data[pos]
                    and pos + 2 < n
                    and data[pos + 2] == data[pos]
                )
            ):
                pos += 1
            out += bytes([pos - start - 1]) + data[start:pos]
        return bytes(out)


PRESET_LZSS = {
    "lzss_classic": (
        "LZSS (ring buffer, Okumura)",
        {
            "window_bits": 12,
            "length_bits": 4,
            "min_match": 3,
            "flags_msb_first": False,
            "set_is_ref": False,
            "ref_format": "okumura",
            "offset_kind": "ring",
            "ring_init": 0x20,
            "ring_start": 0xFEE,
        },
    ),
    "lzss_u16": (
        "LZSS (u16 size, offset-length words)",
        {"size_header": "u16le", "ref_format": "offset_length"},
    ),
}


def register(registry) -> None:
    registry.register(GbaLz77())
    for width in (5, 6, 7):
        registry.register(BitPack(width))
    registry.register(PackBits())
    for pid, (name, params) in PRESET_LZSS.items():
        registry.register(Lzss(params, pid, name))
