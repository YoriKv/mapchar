"""The fill pattern: the bytes that stand in room no text uses.

A pattern of one byte or several, laid from the start of the room it fills and
repeated, the last repeat allowed to be cut short. Where a run of it ends is
told by that rule and not by a byte value, so a pattern is written, measured
and read back the one way (:func:`fill_run`, :func:`fill_end`, :func:`is_fill`).
"""

from __future__ import annotations

import re
from functools import lru_cache

from mapchar.core.bits import bytes_to_bits

DEFAULT_FILL = b"\xff"


def fill_run(fill: bytes, length: int) -> bytes:
    """``length`` bytes of the ``fill`` pattern, from its first byte."""
    if length <= 0:
        return b""
    pattern = fill or DEFAULT_FILL
    return (pattern * -(-length // len(pattern)))[:length]


def is_fill(data: bytes, fill: bytes) -> bool:
    """Whether ``data`` is nothing but the ``fill`` pattern from its first byte,
    the last repeat allowed to be cut short — what :func:`fill_run` lays down."""
    return data == fill_run(fill, len(data))


@lru_cache(maxsize=16)
def _fill_expression(fill: bytes) -> re.Pattern[bytes]:
    """What :func:`is_fill` accepts, as one expression: whole patterns and a
    last one cut short. A fill run is as long as the free space of an expanded
    ROM, so it is measured in one match rather than a pattern at a time."""
    expression = b"(?:" + re.escape(fill) + b")*"
    if len(fill) > 1:
        cut = b"|".join(re.escape(fill[:n]) for n in range(len(fill) - 1, 0, -1))
        expression += b"(?:" + cut + b")?"
    return re.compile(expression)


def fill_end(data: bytes, start: int, fill: bytes, cap: int | None = None) -> int:
    """Where the run of ``fill`` beginning at ``start`` in ``data`` ends.

    The padding :func:`fill_run` would have laid there — whole patterns and a
    last repeat cut short, since the pattern is laid from the start of the
    room it fills — and never past ``cap`` or the end of ``data``.
    """
    limit = len(data) if cap is None else min(cap, len(data))
    if not fill or start >= limit:
        return start
    return _fill_expression(fill).match(data, start, limit).end()


def fill_bits(fill: bytes) -> str:
    """The pattern as bits, spelled the way :meth:`Bits.window` spells them:
    what a reading compares a window against
    (:func:`~mapchar.pipeline.extract.padding_bits`)."""
    return bytes_to_bits(fill)


def parse_fill(text: str) -> bytes:
    """A fill pattern as a configuration spells it: ``$`` and hex digits, a
    byte for every two (``$FFFF`` is two bytes), or a decimal byte."""
    text = text.strip()
    if text.startswith("$"):
        digits = text[1:]
        if not digits:
            raise ValueError("empty fill")
        digits = digits.zfill(len(digits) + len(digits) % 2)
        return bytes.fromhex(digits)
    value = int(text, 10)
    if not 0 <= value <= 0xFF:
        raise ValueError(f"fill {value} is not a byte")
    return bytes([value])


def format_fill(fill: bytes) -> str:
    """``fill`` spelled for a configuration: ``$`` and every byte in hex."""
    return "$" + fill.hex().upper()
