"""The capture setup: what the user gives before playing.

It is kept with the captures (``setup.json``) and filled in again the next
time. The font's location is a range of the ROM image or of one RAM; with one,
the recorder sets a font breakpoint on it (:meth:`Setup.recorder`), whose
first read after a quiet stretch marks the start of a text.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from mapchar.capture.consoles import Console

SETUP = "setup.json"

ROM = "rom"
"""The font's memory when it is in the ROM."""

QUIET = 30
"""Frames without a font read after which the next one starts a text."""


@dataclass(frozen=True)
class Font:
    memory: str
    """:data:`ROM`, or the ``emu.memType`` name of a RAM."""
    start: int
    end: int
    """The first and last byte, as offsets into that memory."""
    bus: int | None = None
    """For a RAM, the bus address its start was given as."""


def rom_font(start: int, end: int, size: int) -> Font:
    """A font in the ROM image, from its first and last offset."""
    if end < start:
        raise ValueError("The font's end comes before its start.")
    if end >= size:
        raise ValueError(f"The ROM ends at ${size - 1:X}.")
    return Font(ROM, start, end)


def ram_font(console: Console, start: int, end: int) -> Font:
    """A font in RAM, from the bus addresses of its first and last byte."""
    if end < start:
        raise ValueError("The font's end comes before its start.")
    first, last = console.ram_of(start), console.ram_of(end)
    if first[0] != last[0] or last[1] - first[1] != end - start:
        raise ValueError("The font must lie in one RAM, in one run of addresses.")
    return Font(first[0], first[1], last[1], start)


@dataclass(frozen=True)
class Setup:
    font: Font | None = None

    @classmethod
    def load(cls, root: str) -> Setup:
        try:
            with open(os.path.join(root, SETUP), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        f = data.get("font")
        return cls(Font(f["memory"], f["start"], f["end"], f.get("bus")) if f else None)

    def save(self, root: str) -> None:
        os.makedirs(root, exist_ok=True)
        f = self.font
        font = (
            {"memory": f.memory, "start": f.start, "end": f.end, "bus": f.bus}
            if f
            else None
        )
        with open(os.path.join(root, SETUP), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"font": font}, fh)

    def recorder(self, console: Console) -> dict:
        """The recorder's settings for this setup: the font breakpoint's
        memory, range and processors — every reader of the ROM, the main CPU
        for a RAM."""
        f = self.font
        if f is None:
            return {}
        rom = f.memory == ROM
        return {
            "font": {
                "memory": console.rom if rom else f.memory,
                "lo": f.start,
                "hi": f.end,
                "cpus": list(console.readers if rom else (console.cpu,)),
            },
            "quiet": QUIET,
        }
