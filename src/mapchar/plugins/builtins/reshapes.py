"""Region-wide byte reorderings, each with its inverse."""

from __future__ import annotations

from mapchar.core.context import PipelineContext
from mapchar.plugins.base import PluginInfo, Stage


class ByteSwap16:
    info = PluginInfo("byteswap16", "Swap byte pairs", Stage.RESHAPE, "Generic")

    def reshape(self, data: bytes, ctx: PipelineContext) -> bytes:
        even = len(data) - len(data) % 2
        out = bytearray(data)
        out[0:even:2], out[1:even:2] = data[1:even:2], data[0:even:2]
        return bytes(out)

    unshape = reshape


_REVERSED = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


class ReverseBits:
    info = PluginInfo("reverse_bits", "Reverse bit order", Stage.RESHAPE, "Generic")

    def reshape(self, data: bytes, ctx: PipelineContext) -> bytes:
        return data.translate(_REVERSED)

    unshape = reshape


class Deinterleave:
    info = PluginInfo("deinterleave", "Deinterleave even/odd", Stage.RESHAPE, "Generic")

    def reshape(self, data: bytes, ctx: PipelineContext) -> bytes:
        return data[0::2] + data[1::2]

    def unshape(self, data: bytes, ctx: PipelineContext) -> bytes:
        half = (len(data) + 1) // 2
        even, odd = data[:half], data[half:]
        out = bytearray(len(data))
        out[0::2] = even
        out[1::2] = odd
        return bytes(out)


def register(registry) -> None:
    for plugin in (ByteSwap16(), ReverseBits(), Deinterleave()):
        registry.register(plugin)
