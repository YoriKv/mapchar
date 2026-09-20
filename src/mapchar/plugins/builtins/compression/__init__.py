"""Built-in compression schemes, and what every one of them owes the pipeline.

Each decompresses from the start of its input, publishes how many bytes it
consumed (``KEY_CONSUMED``) and whether it saw its own end (``KEY_COMPLETE``),
bounds what one read may produce, and compresses back where the scheme allows.
:mod:`~mapchar.plugins.builtins.compression._limits` states that contract; the
shared match search and run packer live in ``_lz`` and ``_rle``.

A scheme with **no end to find** — PackBits, RLE2, bit-packed text, a ring LZSS
with no size prefix, Huffman with no end symbol — reports ``KEY_COMPLETE`` false
always, so Scan cannot find one and a block over one carries an explicit length.
"""

from __future__ import annotations

from mapchar.plugins.builtins.compression.bitpack import BitPack
from mapchar.plugins.builtins.compression.huffman import HuffmanTable
from mapchar.plugins.builtins.compression.konami_rle import KonamiFdsRle, KonamiNesRle
from mapchar.plugins.builtins.compression.kosinski import Kosinski
from mapchar.plugins.builtins.compression.lz_command import (
    Lz1,
    Lz1Improved,
    Lz2,
    Lz2Improved,
)
from mapchar.plugins.builtins.compression.lzss import (
    GBA_LZ77,
    PRESET_LZSS,
    GbaLz77,
    Lzss,
    presets,
)
from mapchar.plugins.builtins.compression.packbits import PackBits
from mapchar.plugins.builtins.compression.prs import Prs
from mapchar.plugins.builtins.compression.rnc import Rnc1, Rnc2
from mapchar.plugins.builtins.compression.snes_rle import Rle1, Rle2

__all__ = [
    "GBA_LZ77",
    "PRESET_LZSS",
    "BitPack",
    "GbaLz77",
    "HuffmanTable",
    "KonamiFdsRle",
    "KonamiNesRle",
    "Kosinski",
    "Lz1",
    "Lz1Improved",
    "Lz2",
    "Lz2Improved",
    "Lzss",
    "PackBits",
    "Prs",
    "Rle1",
    "Rle2",
    "Rnc1",
    "Rnc2",
    "register",
]


def register(registry) -> None:
    for plugin in (
        *presets(),
        Lz1(),
        Lz1Improved(),
        Lz2(),
        Lz2Improved(),
        Kosinski(),
        Prs(),
        Rnc1(),
        Rnc2(),
        Rle1(),
        Rle2(),
        KonamiNesRle(),
        KonamiFdsRle(),
        PackBits(),
    ):
        registry.register(plugin)
    for width in (5, 6, 7):
        registry.register(BitPack(width))
