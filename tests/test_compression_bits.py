"""The fixed-width bit packings and the Huffman table: two schemes that carry
symbols rather than bytes, and neither of which says where a structure ends."""

from __future__ import annotations

import pytest

from compression_helpers import decode
from mapchar.core.context import PipelineContext
from mapchar.plugins.builtins.compression import BitPack, HuffmanTable
from mapchar.plugins.builtins.compression._limits import MAX_OUT


def test_bitpack_has_no_end_and_is_bounded() -> None:
    bp = BitPack(5)
    data = bytes([1, 2, 3, 31, 0, 17])
    ctx = PipelineContext()
    packed = bp.compress(data, ctx)
    assert len(packed) == 4
    out, consumed, complete = decode(bp, packed)
    assert out[: len(data)] == data and consumed == len(packed)
    # The packing says nothing about where a structure ends; the alphabet's own
    # terminator token does, one table stage later.
    assert complete is False
    assert len(decode(bp, b"\xff" * (MAX_OUT // 2))[0]) <= MAX_OUT


def node(left: int, right: int) -> bytes:
    """One 4-byte Huffman node: left link, right link, leaves flagged 0x8000."""
    return left.to_bytes(2, "little") + right.to_bytes(2, "little")


TREE = node(0x8000 | 0x41, 1) + node(0x8000 | 0x42, 0x8000 | 0x43)
"""root -> (A | node1), node1 -> (B | C)."""


def test_huffman_table_roundtrip() -> None:
    huff = HuffmanTable({"tree": TREE, "end_symbol": 0x43})
    ctx = PipelineContext()
    packed = huff.compress(b"ABAC", ctx)
    assert packed == bytes([0b01001100])  # 0 10 0 11, zero-padded
    out, consumed, complete = decode(huff, packed + b"\xff")
    assert out == b"ABAC" and complete and consumed == 1


def test_huffman_without_an_end_symbol_is_never_complete() -> None:
    huff = HuffmanTable({"tree": TREE})
    out, _, complete = decode(huff, bytes([0b01001100]))
    assert out.startswith(b"ABAC") and complete is False
    # With an end symbol, running out of bits before it is a truncated stream.
    ended = HuffmanTable({"tree": TREE, "end_symbol": 0x43})
    with pytest.raises(ValueError, match="no end symbol"):
        decode(ended, bytes([0b00000000]))
    out, _, complete = decode(ended, bytes([0b00000000]), partial=True)
    assert out == b"AAAAAAAA" and complete is False


def test_huffman_binds_tree_from_rom() -> None:
    rom = b"\xff" * 32 + TREE
    huff = HuffmanTable({"tree_offset": 32, "end_symbol": 0x43})
    huff.bind_tree(rom)
    assert decode(huff, bytes([0b01001100]))[0] == b"ABAC"
