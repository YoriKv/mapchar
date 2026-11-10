"""Reading and writing a pointer's bytes.

A pointer's *value* becomes a payload offset through a mapping plugin
(:class:`~mapchar.plugins.base.Mapping`); these helpers are the bytes on
either side of that conversion. A mapping never learns how wide the pointer
is, so a ``signed`` one's value is sign-extended here, by whoever read it
(:func:`pointer_offset`), and checked against the width it is written back in
(:func:`holds`).
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


def is_signed(mapping) -> bool:
    """Whether ``mapping`` reads a value as two's complement at the pointer's
    own size: its optional ``signed``, absent read as unsigned."""
    return bool(getattr(mapping, "signed", False))


def sign_extend(value: int, size: int) -> int:
    """``value``, read unsigned from ``size`` bytes, as a two's-complement
    number of that width."""
    half = 1 << (size * 8 - 1)
    return ((value & ((half << 1) - 1)) ^ half) - half


def holds(value: int, size: int, signed: bool) -> bool:
    """Whether a pointer of ``size`` bytes can hold ``value``: unsigned, or
    with ``signed``, as two's complement."""
    bits = size * 8
    if signed:
        return -(1 << (bits - 1)) <= value < 1 << (bits - 1)
    return 0 <= value < 1 << bits


def pointer_offset(
    mapping, value: int, size: int, bank: int = 0, ptr_address: int = 0
) -> int | None:
    """Where a pointer of ``size`` bytes at ``ptr_address`` holding ``value``,
    read unsigned, reaches through ``mapping``: sign-extended first when the
    mapping is ``signed``."""
    if is_signed(mapping):
        value = sign_extend(value, size)
    return mapping.to_offset(value, bank, ptr_address)
