"""The number spelling every table, script and command file shares.

``$`` marks hex, anywhere a number is written; a bare number is decimal.
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
