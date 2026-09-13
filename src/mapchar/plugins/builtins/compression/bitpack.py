"""Fixed-width symbols packed MSB-first — the 5- and 6-bit text alphabets.

One byte per symbol comes out, so a 5-bit alphabet fits 1.6 symbols per byte.
**There is no end marker**: the packing says nothing about where a string or a
structure ends (the alphabet's own terminator token does, one table stage later),
so ``KEY_COMPLETE`` is never reported and a Scan cannot find these. Give a
bit-packed block an explicit length.

Output is bounded at :data:`~mapchar.plugins.builtins.compression._limits.MAX_OUT`
and only the input that feeds it is unpacked, an unbounded read otherwise
expanding the whole file tail by 8/``width``.
"""

from __future__ import annotations

from mapchar.core.bits import bits_to_bytes, bytes_to_bits
from mapchar.core.context import KEY_COMPLETE, KEY_CONSUMED, PipelineContext
from mapchar.plugins.base import PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_OUT


class BitPack:
    def __init__(self, width: int):
        self.width = width
        self.info = PluginInfo(
            f"bitpack{width}",
            f"{width}-bit packed symbols",
            Stage.COMPRESSION,
            "Generic",
        )

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        # Unpack only the bytes the cap can hold: bytes_to_bits over a whole ROM
        # tail would build the expansion this cap exists to prevent.
        usable = min(len(data), -(-MAX_OUT * self.width // 8))
        bits = bytes_to_bits(data[:usable])
        n = min(len(bits) // self.width, MAX_OUT)
        out = bytes(
            int(bits[i * self.width : (i + 1) * self.width], 2) for i in range(n)
        )
        ctx.set(KEY_CONSUMED, -(-n * self.width // 8))
        # No end marker: where the buffer ran out is not where a structure ended.
        ctx.set(KEY_COMPLETE, False)
        return out

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        bits = "".join(
            format(b & ((1 << self.width) - 1), f"0{self.width}b") for b in data
        )
        return bits_to_bytes(bits)
