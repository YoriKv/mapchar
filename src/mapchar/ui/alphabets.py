"""The canned runs of characters a fill offers, and what each is called.

One list, so the Table Editor's Fill dialog and the relative search's results
name the same alphabets the same way — a hit on ``A-Z`` and the fill that lays
``A-Z`` down are the one idea, and a user who read the first picks the second.
"""

from __future__ import annotations

from mapchar.engines.relsearch import (
    DIGIT,
    HIRAGANA,
    KATAKANA,
    LOWER,
    RUNS,
    UPPER,
)

ASCII_PRINTABLE = "".join(chr(c) for c in range(0x20, 0x7F))

RUN_NAMES: dict[str, str] = {
    UPPER: "A-Z",
    LOWER: "a-z",
    DIGIT: "0-9",
    HIRAGANA: "あ-ん",
    KATAKANA: "ア-ン",
}
"""What a relative search's runs are called wherever an alphabet is offered."""

ALPHABETS: dict[str, str] = {
    "A-Z": RUNS[UPPER],
    "a-z": RUNS[LOWER],
    "0-9": RUNS[DIGIT],
    "A-Z a-z 0-9": RUNS[UPPER] + RUNS[LOWER] + RUNS[DIGIT],
    "ASCII printable": ASCII_PRINTABLE,
    "あ-ん": RUNS[HIRAGANA],
    "ア-ン": RUNS[KATAKANA],
}
"""Each run by the alphabet it spells, in the order a picker offers them."""

CUSTOM = "Custom…"
"""The row that asks for the characters instead of naming them."""

__all__ = ["ALPHABETS", "CUSTOM", "RUN_NAMES"]
