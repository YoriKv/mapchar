"""Bit-addressed access to a byte buffer.

Every position and length inside ``core`` and ``engines`` is in bits, so that
odd-width table entries and bit-packed text need no special case. ``Bits``
gives windows of a byte buffer as strings of ``'0'``/``'1'`` without ever
materialising the whole buffer as one.
"""

from __future__ import annotations

_CHUNK_BITS = 4096 * 8
"""How much of the buffer one cached bit string spells."""


class Bits:
    """The buffer is spelled as bits a chunk at a time, on first use, and each
    chunk kept: a decode asks for a window at nearly every bit, and spelling
    the bytes under each ask over again costs more than the ask."""

    __slots__ = ("_chunks", "_data", "length")

    def __init__(self, data: bytes):
        self._data = bytes(data)
        self.length = len(self._data) * 8
        self._chunks: dict[int, str] = {}

    @property
    def data(self) -> bytes:
        return self._data

    def _chunk(self, index: int) -> str:
        text = self._chunks.get(index)
        if text is None:
            chunk = self._data[index * 4096 : (index + 1) * 4096]
            text = format(int.from_bytes(chunk, "big"), f"0{len(chunk) * 8}b")
            self._chunks[index] = text
        return text

    def window(self, pos: int, n: int) -> str:
        """Up to ``n`` bits starting at bit ``pos``, clipped to the buffer."""
        if pos < 0:
            raise ValueError("negative bit position")
        end = min(pos + n, self.length)
        if end <= pos:
            return ""
        index, at = divmod(pos, _CHUNK_BITS)
        if at + (end - pos) <= _CHUNK_BITS:
            return self._chunk(index)[at : at + (end - pos)]
        parts = [self._chunk(index)[at:]]
        pos = (index + 1) * _CHUNK_BITS
        while pos < end:
            parts.append(self._chunk(pos // _CHUNK_BITS)[: end - pos])
            pos += _CHUNK_BITS
        return "".join(parts)


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


def format_key(bits: str) -> str:
    """``bits`` as hex digits, or ``%bits`` when they are not whole nibbles."""
    if len(bits) % 4:
        return "%" + bits
    return bits_to_hex(bits)


def parse_hex(text: str, default: int = 0) -> int:
    """A hex number written with any of ``$``, ``0x`` or ``_``; empty is ``default``."""
    text = text.strip().replace("$", "").replace("0x", "").replace("_", "")
    if not text:
        return default
    return int(text, 16)


def align_up(pos: int, multiple: int, offset: int = 0) -> int:
    """The first position at or after ``pos`` that is ``offset`` past a multiple.

    A ``multiple`` of zero or less leaves ``pos`` alone; anything at or before
    ``offset`` lands on ``offset``.
    """
    if multiple <= 0:
        return pos
    rel = pos - offset
    if rel <= 0:
        return offset
    return -(-rel // multiple) * multiple + offset


_REVERSED = bytes(int(format(b, "08b")[::-1], 2) for b in range(256))


def reverse_bits(data: bytes) -> bytes:
    """Every byte with its bits in the opposite order."""
    return data.translate(_REVERSED)
