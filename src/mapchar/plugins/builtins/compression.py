"""Built-in text compression schemes.

Each decompresses from the start of its input, publishes how many bytes it
consumed (``KEY_CONSUMED``) and whether it saw its end (``KEY_COMPLETE``),
and compresses back where the scheme allows.
"""

from __future__ import annotations

from mapchar.core.context import KEY_COMPLETE, KEY_CONSUMED, PipelineContext
from mapchar.plugins.base import PluginInfo, Stage


class GbaLz77:
    """GBA/NDS BIOS LZ77: ``10 SS SS SS`` header, 8-flag groups, 12-bit back
    references of 3..18 bytes."""

    info = PluginInfo("gba_lz77", "GBA BIOS LZ77", Stage.COMPRESSION, "Nintendo")

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        if len(data) < 4 or data[0] != 0x10:
            raise ValueError("not an LZ77 stream (no 10 header)")
        size = int.from_bytes(data[1:4], "little")
        out = bytearray()
        pos = 4
        complete = False
        while len(out) < size and pos < len(data):
            flags = data[pos]
            pos += 1
            for bit in range(8):
                if len(out) >= size:
                    break
                if pos >= len(data):
                    break
                if flags & (0x80 >> bit):
                    if pos + 1 >= len(data):
                        pos = len(data)
                        break
                    hi, lo = data[pos], data[pos + 1]
                    pos += 2
                    length = (hi >> 4) + 3
                    disp = ((hi & 0x0F) << 8 | lo) + 1
                    for _ in range(length):
                        if len(out) < disp:
                            raise ValueError("back reference before the start")
                        out.append(out[-disp])
                else:
                    out.append(data[pos])
                    pos += 1
        complete = len(out) >= size
        ctx.set(KEY_CONSUMED, pos)
        ctx.set(KEY_COMPLETE, complete)
        return bytes(out[:size])

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        out = bytearray(b"\x10" + len(data).to_bytes(3, "little"))
        pos = 0
        n = len(data)
        while pos < n:
            flags = 0
            group = bytearray()
            for bit in range(8):
                if pos >= n:
                    break
                best_len, best_disp = 0, 0
                window_start = max(0, pos - 0x1000)
                for start in range(window_start, pos):
                    length = 0
                    while (
                        length < 18
                        and pos + length < n
                        and data[start + length] == data[pos + length]
                    ):
                        length += 1
                    if length > best_len:
                        best_len, best_disp = length, pos - start
                        if length == 18:
                            break
                if best_len >= 3:
                    flags |= 0x80 >> bit
                    hi = ((best_len - 3) << 4) | ((best_disp - 1) >> 8)
                    group += bytes([hi, (best_disp - 1) & 0xFF])
                    pos += best_len
                else:
                    group.append(data[pos])
                    pos += 1
            out.append(flags)
            out += group
        while len(out) % 4:
            out.append(0)
        return bytes(out)


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
        bits = "".join(f"{b:08b}" for b in data)
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
        bits += "0" * (-len(bits) % 8)
        return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


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
        stream = "".join(bits)
        stream += "0" * (-len(stream) % 8)
        out = bytearray()
        for i in range(0, len(stream), 8):
            chunk = stream[i : i + 8]
            if self.lsb_first:
                chunk = chunk[::-1]
            out.append(int(chunk, 2))
        return bytes(out)


def register(registry) -> None:
    registry.register(GbaLz77())
    for width in (5, 6, 7):
        registry.register(BitPack(width))
