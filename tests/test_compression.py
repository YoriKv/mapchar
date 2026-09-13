from __future__ import annotations

from mapchar.core.context import KEY_COMPLETE, KEY_CONSUMED, PipelineContext
from mapchar.plugins.base import Stage
from mapchar.plugins.builtins.compression import BitPack, GbaLz77, HuffmanTable
from mapchar.plugins.registry import default_registry


def test_lz77_roundtrip_and_consumed():
    text = b"HELLO HELLO HELLO WORLD, HELLO WORLD! " * 6
    lz = GbaLz77()
    ctx = PipelineContext()
    packed = lz.compress(text, ctx)
    assert len(packed) < len(text)
    out = lz.decompress(packed + b"\xaa" * 7, ctx)
    assert out == text
    assert len(packed) - 3 <= ctx.get(KEY_CONSUMED) <= len(packed)
    assert ctx.get(KEY_COMPLETE)
    short = lz.decompress(packed[: len(packed) // 2], PipelineContext())
    assert text.startswith(short)


def test_bitpack():
    bp = BitPack(5)
    data = bytes([1, 2, 3, 31, 0, 17])
    ctx = PipelineContext()
    packed = bp.compress(data, ctx)
    assert len(packed) == 4
    assert bp.decompress(packed, ctx)[: len(data)] == data


def test_huffman_table_roundtrip():
    # Nodes of 4 bytes: left link, right link; leaves flagged with 0x8000.
    # Tree: root -> (A | node1), node1 -> (B | C).
    def node(left, right):
        return left.to_bytes(2, "little") + right.to_bytes(2, "little")

    tree = node(0x8000 | 0x41, 1) + node(0x8000 | 0x42, 0x8000 | 0x43)
    huff = HuffmanTable({"tree": tree, "end_symbol": 0x43})
    ctx = PipelineContext()
    packed = huff.compress(b"ABAC", ctx)
    assert packed == bytes([0b01001100])  # 0 10 0 11, zero-padded
    out = huff.decompress(packed + b"\xff", ctx)
    assert out == b"ABAC" and ctx.get(KEY_COMPLETE) and ctx.get(KEY_CONSUMED) == 1


def test_registered():
    reg = default_registry()
    assert reg.plugin(Stage.COMPRESSION, "gba_lz77") is not None
    assert reg.plugin(Stage.COMPRESSION, "bitpack6") is not None
