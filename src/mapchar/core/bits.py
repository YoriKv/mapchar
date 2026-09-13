"""Bit-addressed access to a byte buffer.

Every position and length inside ``core`` and ``engines`` is in bits, so that
odd-width table entries and bit-packed text need no special case. ``Bits``
gives windows of a byte buffer as strings of ``'0'``/``'1'`` without ever
materialising the whole buffer as one.
"""

from __future__ import annotations


class Bits:
    __slots__ = ("_data", "length")

    def __init__(self, data: bytes):
        self._data = bytes(data)
        self.length = len(self._data) * 8

    @property
    def data(self) -> bytes:
        return self._data

    def window(self, pos: int, n: int) -> str:
        """Up to ``n`` bits starting at bit ``pos``, clipped to the buffer."""
        if pos < 0:
            raise ValueError("negative bit position")
        end = min(pos + n, self.length)
        if end <= pos:
            return ""
        first, last = pos // 8, (end - 1) // 8
        chunk = self._data[first : last + 1]
        value = int.from_bytes(chunk, "big")
        text = format(value, f"0{len(chunk) * 8}b")
        start = pos - first * 8
        return text[start : start + (end - pos)]

    def byte_at_bit(self, pos: int) -> int:
        """The byte holding bit ``pos``; used by the raw view's hex column."""
        return self._data[pos // 8]


def bits_to_bytes(bits: str) -> bytes:
    """Pack a bit string MSB-first, zero-padding the last byte."""
    if not bits:
        return b""
    pad = (-len(bits)) % 8
    return int(bits + "0" * pad, 2).to_bytes((len(bits) + pad) // 8, "big")


def bytes_to_bits(data: bytes) -> str:
    if not data:
        return ""
    return format(int.from_bytes(data, "big"), f"0{len(data) * 8}b")


def hex_to_bits(digits: str) -> str:
    """``4`` bits per hex digit, leading zeros kept."""
    return "".join(format(int(d, 16), "04b") for d in digits)


def bits_to_hex(bits: str) -> str:
    """The inverse of :func:`hex_to_bits` for whole-nibble strings."""
    if len(bits) % 4:
        raise ValueError("not a whole number of nibbles")
    return "".join(format(int(bits[i : i + 4], 2), "X") for i in range(0, len(bits), 4))
