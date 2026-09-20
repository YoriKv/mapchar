"""Huffman text through a node table stored in the ROM."""

from __future__ import annotations

from mapchar.core.bits import bits_to_bytes, reverse_bits
from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_OUT, stream_error

_SCHEME = "Huffman"


class HuffmanTable(PartialDecompression):
    """Huffman text through a node table stored in the ROM.

    Parameters name where the tree lives and how a node is laid out: nodes
    are ``node_size`` bytes, holding a left and a right link at byte offsets
    ``left``/``right`` (little-endian, ``link_size`` bytes). A link with the
    ``leaf_flag`` bit set is a leaf whose symbol is the link masked by
    ``leaf_mask``; otherwise it indexes another node. Decoding starts at
    ``root`` and reads bits MSB-first (``lsb_first`` flips that), one symbol
    per leaf, until the ``end_symbol`` or the data runs out. Compression
    walks the same tree backwards.

    ``end_symbol`` is the scheme's only end marker, so without one no decode can
    report a complete structure and a Scan cannot find these; with one, running
    out of bits before it is a truncated stream rather than a structure.
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
        if at < 0 or at + self.link_size > len(self.tree):
            # The tree is at fault, not the stream, and ``_build_paths`` walks it
            # to compress as well: not a ``stream_error``.
            raise ValueError(f"node {node} is past the end of the Huffman tree")
        return int.from_bytes(self.tree[at : at + self.link_size], "little")

    def _decode(self, data: bytes, *, partial: bool) -> tuple[bytes, int, bool]:
        out = bytearray()
        node = self.root
        # Both halves are capped: the output for memory, the input because a tree
        # whose leaves are never reached would otherwise walk the whole file bit
        # by bit on every refresh.
        total_bits = min(len(data), MAX_OUT) * 8
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
                if len(out) >= MAX_OUT:
                    break
            else:
                node = link
        if self.end_symbol is None:
            # No end marker: where the bits ran out is not where a structure did.
            return bytes(out), -(-consumed_bits // 8), False
        if not complete and not partial:
            raise stream_error(_SCHEME, f"no end symbol in {len(out):,} decoded bytes")
        return bytes(out), -(-consumed_bits // 8), complete

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

    def _encode(self, data: bytes) -> bytes:
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
