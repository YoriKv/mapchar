"""Reading and writing a pointer's bytes.

A pointer's *value* becomes a payload offset through a mapping plugin
(:class:`~mapchar.plugins.base.Mapping`); these two helpers are the bytes on
either side of that conversion.
"""

from __future__ import annotations


def read_pointer(data: bytes, address: int, size: int, endian: str) -> int | None:
    chunk = data[address : address + size]
    if len(chunk) < size:
        return None
    return int.from_bytes(chunk, "big" if endian == "big" else "little")


def pointer_bytes(value: int, size: int, endian: str) -> bytes:
    return (value & ((1 << (size * 8)) - 1)).to_bytes(
        size, "big" if endian == "big" else "little"
    )
