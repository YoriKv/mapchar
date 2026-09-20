"""The number spelling every table, script and command file shares.

``$`` marks hex, anywhere a number is written; a bare number is decimal. The
UI's offset fields are the exception: always hex, so ``$`` is optional there
(:func:`parse_hex_offset`).
"""

from __future__ import annotations

NUM = r"\$[0-9A-Fa-f]+|\d+"
"""An unsigned number as this spelling writes it, for an expression that has to
find one in a line: ``$hex`` or decimal, which :func:`parse_num` then reads.
Bracket it where it sits beside anything else — it is an alternation."""


def parse_num(text: str) -> int:
    """``$hex``, ``-$hex``, ``$-hex`` or decimal. Raises ``ValueError``."""
    text = text.strip()
    if text.startswith(("$-", "-$")):
        return -int(text[2:], 16)
    if text.startswith("$"):
        return int(text[1:], 16)
    return int(text, 10)


def format_num(value: int) -> str:
    """``$hex``, with the sign before the digits: ``$-hex``."""
    return f"${value:X}" if value >= 0 else f"$-{-value:X}"


_HEX_DIGITS = "0123456789abcdefABCDEF"


def _scan_hex(text: str, signed: bool) -> tuple[int, str]:
    """``(sign, digits)`` of a hex number written with ``$``, ``0x`` or ``_``.

    ``digits`` is empty for blank text, which each caller answers its own way;
    anything else that is not hex raises ``ValueError``.
    """
    digits, sign = text.strip(), 1
    if signed:
        for prefix in ("-$", "$-", "-", "$"):
            if digits.startswith(prefix):
                digits, sign = digits[len(prefix) :], -1 if "-" in prefix else 1
                break
    else:
        digits = digits.removeprefix("$")
    digits = digits.removeprefix("0x").removeprefix("0X").replace("_", "")
    if digits and not all(c in _HEX_DIGITS for c in digits):
        raise ValueError(f"not a hex number: {text!r}")
    return sign, digits


def parse_hex(text: str, default: int | None = 0) -> int | None:
    """A hex number written with any of ``$``, ``0x`` or ``_``; empty is ``default``."""
    sign, digits = _scan_hex(text, signed=True)
    return default if not digits else sign * int(digits, 16)


def parse_hex_offset(text: str) -> int:
    """A signed hex offset — ``1F0``, ``$1F0``, ``-10``, ``-$10`` or ``$-10``.

    Raises ``ValueError``, blank text included.
    """
    sign, digits = _scan_hex(text, signed=True)
    if not digits:
        raise ValueError(f"not a hex offset: {text!r}")
    return sign * int(digits, 16)


def parse_flat_hex(text: str) -> int | None:
    """An unsigned hex offset, or ``None`` for blank text or text nothing reads."""
    try:
        _, digits = _scan_hex(text, signed=False)
    except ValueError:
        return None
    return int(digits, 16) if digits else None


def format_hex_offset(value: int) -> str:
    """A signed hex offset as the UI's offset fields spell it: ``1F0``, ``-10``."""
    return f"{value:X}" if value >= 0 else f"-{-value:X}"


def clamp(value: int, lo: int, hi: int) -> int:
    """``value`` brought inside ``[lo, hi]``. An empty range — ``hi`` below
    ``lo`` — gives ``lo``, so a caller whose upper bound can fall away is not
    handed something below its lower one."""
    return max(lo, min(value, hi))
