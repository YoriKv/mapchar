"""Qt-free helpers the compression test modules share: the stage call the
pipeline makes, the hex literal, and the LZSS family by plugin id.

Those modules pin a scheme to bytes assembled from its format description
before asking it to round-trip, because a vector is what a round trip cannot
catch. Every scheme has at least one field that fails *silently* when read the
wrong way round — which end of a flag byte the first op sits at, whether a set
bit means the reference or the literal, which nibble holds the length, what a
distance is biased by — and a compressor that makes the same mistake as its
decompressor round-trips perfectly.
"""

from __future__ import annotations

from mapchar.core.context import (
    KEY_COMPLETE,
    KEY_CONSUMED,
    KEY_DECOMPRESS_PARTIAL,
    PipelineContext,
)
from mapchar.plugins.builtins.compression.lzss import presets

SCHEMES = {p.info.id: p for p in presets()}
"""The LZSS family by plugin id, so a test names a framing as the UI does."""


def decode(plugin, data: bytes, *, partial: bool = False):
    """``(output, consumed, complete)`` through the stage, as the pipeline runs it."""
    ctx = PipelineContext()
    if partial:
        ctx.set(KEY_DECOMPRESS_PARTIAL, True)
    out = plugin.decompress(data, ctx)
    return out, ctx.get(KEY_CONSUMED), ctx.get(KEY_COMPLETE)


def hexs(*parts: str) -> bytes:
    return bytes.fromhex("".join(parts))
