"""The number spelling every table, script and command file shares.

``$`` marks hex, anywhere a number is written; a bare number is decimal. The
UI's offset fields are the exception: always hex, so ``$`` is optional there
(:func:`parse_hex_offset`).
"""

from __future__ import annotations


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


def parse_hex_offset(text: str) -> int:
    """A signed hex offset — ``1F0``, ``$1F0``, ``-10``, ``-$10`` or ``$-10``.

    Raises ``ValueError``, blank text included.
    """
    digits, sign = text.strip(), 1
    for prefix in ("-$", "$-", "-", "$"):
        if digits.startswith(prefix):
            digits, sign = digits[len(prefix) :], -1 if "-" in prefix else 1
            break
    digits = digits.removeprefix("0x").removeprefix("0X")
    if not digits or not all(c in "0123456789abcdefABCDEF_" for c in digits):
        raise ValueError(f"not a hex offset: {text!r}")
    return sign * int(digits, 16)


def format_hex_offset(value: int) -> str:
    """A signed hex offset as the UI's offset fields spell it: ``1F0``, ``-10``."""
    return f"{value:X}" if value >= 0 else f"-{-value:X}"
