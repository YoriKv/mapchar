"""The compression stage as a whole: what ships, that every scheme reads back
what it writes and its own extent, and that each is smaller than what it was
given."""

from __future__ import annotations

import random

import pytest

from compression_helpers import SCHEMES, decode
from mapchar.core.context import PipelineContext
from mapchar.plugins.base import Stage, writes_back
from mapchar.plugins.builtins.compression import Lzss
from mapchar.plugins.builtins.compression.lzss import PRESET_LZSS

ROUND_TRIP = [
    # Not the empty payload: only SLZ encodes one, the size-prefixed framings
    # rejecting a zero there so that header-shaped noise is not a structure.
    b"A",
    b"A" * 40,
    b"ABCABCABCABC HELLO HELLO HELLO WORLD" * 5,
    b"HELLO HELLO HELLO WORLD, HELLO WORLD! " * 6 + bytes(range(64)),
    bytes(300),
    bytes(random.Random(1105).randrange(256) for _ in range(2000)),
]


def test_every_registered_scheme_round_trips(registry) -> None:
    """Whatever a scheme writes, it reads back — and reads back its own extent.

    ``consumed`` may be less than the stream where a framing ends with a flag or
    descriptor byte nobody reads, so it is the content's length rather than the
    slot's; it may never claim bytes the stream does not have.
    """
    for plugin in registry.plugins(Stage.COMPRESSION):
        assert writes_back(plugin, Stage.COMPRESSION), plugin.info.id
        for data in ROUND_TRIP:
            width = getattr(plugin, "width", 0)
            if width:  # a bit packing carries symbols, not bytes
                data = bytes(b & ((1 << width) - 1) for b in data)
            ctx = PipelineContext()
            packed = plugin.compress(data, ctx)
            out, consumed, _ = decode(plugin, packed)
            assert out[: len(data)] == data, (plugin.info.id, len(data))
            assert width or out == data, (plugin.info.id, len(data))
            assert consumed <= len(packed), plugin.info.id


def test_registered(registry) -> None:
    for pid in (
        "gba_lz77",
        "bitpack6",
        "packbits",
        "lz1",
        "lz2",
        "lz2_improved",
        "kosinski",
        "prs",
        "rnc1",
        "rnc2",
        "rle1",
        "rle2",
        "konami_nes_rle",
        "konami_fds_rle",
        *PRESET_LZSS,
    ):
        assert registry.plugin(Stage.COMPRESSION, pid) is not None, pid
    # Exactly what ships, because the features doc lists it: the bit packings
    # are registered ready-made, and Huffman is the one scheme with nothing to
    # register — its node layout and tree address *are* the format, so it
    # reaches the registry only through a preset.
    assert set(registry.ids(Stage.COMPRESSION)) == {
        "gba_lz77",
        *PRESET_LZSS,
        "lz1",
        "lz1_improved",
        "lz2",
        "lz2_improved",
        "kosinski",
        "prs",
        "rnc1",
        "rnc2",
        "rle1",
        "rle2",
        "konami_nes_rle",
        "konami_fds_rle",
        "packbits",
        "bitpack5",
        "bitpack6",
        "bitpack7",
    }
    # The engine is what a preset TOML names, so its parameters are the contract.
    assert isinstance(SCHEMES["slz16"], Lzss)
    with pytest.raises(ValueError, match="window_bits"):
        Lzss({"window_bits": 13})
    with pytest.raises(ValueError, match="size_header"):
        Lzss({"size_header": "u24le"})


def test_compression_compresses(registry) -> None:
    """Every scheme is smaller than what it was given, on data it is for."""
    data = b"HELLO HELLO HELLO WORLD, HELLO WORLD! " * 6 + b"\x00" * 200
    for plugin in registry.plugins(Stage.COMPRESSION):
        if getattr(plugin, "width", 0):
            continue  # a fixed width packs by 8/width whatever the bytes are
        packed = plugin.compress(data, PipelineContext())
        assert len(packed) < len(data), plugin.info.id
