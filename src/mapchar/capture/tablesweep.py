"""Every string of a pointer table, shown in the game's own text box.

A capture whose trace confirmed the pointer it was shown through — a slot of
the table its engine reads — is run again once per string of that table, with
the slot changed to hold that string's pointer: from the latest state before
the game read the slot, to the frame the text settled (the pause, for text
seen in RAM), where a screenshot is taken. No message id of the game is
needed, only the slot.

A string that waits for a button, or draws past the screenshot's frame, is
shown as it stands at that frame.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from mapchar.capture.consoles import Console
from mapchar.capture.emulator import Emulator
from mapchar.capture.evidence import EVIDENCE, Moment, parse_line
from mapchar.capture.probe import ProbeServer
from mapchar.capture.protocol import CaptureError, Step
from mapchar.capture.trace import Result
from mapchar.core.block import BlockConfig, PointerTableSource

MAX_STRINGS = 64
"""The most strings one run shows."""

SCAN_LINES = 50000
"""Evidence lines read between two yields."""

Shot = tuple[int, str]
"""What a sweep yields for each string: its index in the table and the
screenshot's path."""


@dataclass(frozen=True)
class Slots:
    """A pointer table as offsets into the ROM image."""

    start: int
    stop: int
    """Past the last slot's last byte."""
    size: int
    stride: int

    @property
    def count(self) -> int:
        if self.stride <= 0 or self.stop - self.start < self.size:
            return 0
        return (self.stop - self.start - self.size) // self.stride + 1

    def slot(self, index: int) -> int:
        return self.start + index * self.stride


def table_of(config: BlockConfig | None, shift: int) -> Slots | None:
    """The pointer table a block proposal reads, in the ROM image (its
    offsets are the payload's, ``shift`` past the image's)."""
    src = config.source if config is not None else None
    if not isinstance(src, PointerTableSource):
        return None
    t = Slots(src.start - shift, src.stop - shift, src.size, src.stride)
    return t if t.count and t.start >= 0 else None


def own_slot(result: Result | None, table: Slots) -> int | None:
    """The table's slot the capture's string was reached through: one with a
    byte whose change the trace saw move the string."""
    if result is None:
        return None
    moved = {p.address for p in result.pointers}
    for i in range(table.count):
        s = table.slot(i)
        if any(s + k in moved for k in range(table.size)):
            return s
    return None


def pointer_frame(
    folder: str, console: Console, slot: int, size: int, result: Result
) -> Step[int | None]:
    """The frame of the game's last read of the slot before the string's
    first read, from the capture's evidence, its lines read as the evidence
    reads them; None when it is not there."""
    first = result.first_read
    if first is None and result.string is not None:
        first = result.string[0]
    frame = Moment.load(folder).state(1).frame
    last = None
    try:
        with open(
            os.path.join(folder, EVIDENCE), encoding="ascii", errors="replace"
        ) as fh:
            for n, line in enumerate(fh):
                if n % SCAN_LINES == 0:
                    yield
                if line.startswith("F "):
                    try:
                        frame = int(line[2:])
                    except ValueError:
                        pass
                    continue
                if not line.startswith("E "):
                    continue
                try:
                    e = parse_line(line.rstrip("\n"), console)
                except ValueError:  # a mapping that has no offset for it
                    continue
                if e is None:
                    continue
                off = e[1]
                if slot <= off < slot + size:
                    last = frame
                elif off == first and last is not None:
                    return last
    except OSError:
        return None
    return last


def sweep(
    emulator: Emulator,
    rom_path: str,
    rom: bytes,
    console: Console,
    folder: str,
    result: Result | None,
    table: Slots,
    first: int = 0,
    limit: int = MAX_STRINGS,
) -> Step[int]:
    """Screenshot the strings of ``table`` from ``first``, ``limit`` of them
    at most, each shown through the capture's own slot. Yields a
    :data:`Shot` for each as it is taken (and ``WAIT`` or ``None`` between),
    and returns the index after the last shown. Closed before its end, its
    emulator is killed."""
    slot = own_slot(result, table)
    if slot is None:
        raise CaptureError("the capture's pointer is not one of this table's")
    moment = Moment.load(folder)
    at = yield from pointer_frame(folder, console, slot, table.size, result)
    if at is None:
        at = result.frames[0] if result.frames[0] else moment.state(1).frame
    shot_at = result.settled or moment.frame
    end = min(table.count, first + limit)
    p = ProbeServer(emulator, rom_path, console, folder, name="sweep")
    # A shot runs from the latest state before its frame: none taken after
    # the game read the slot, so the changed slot is what it reads — also
    # after the server restarts.
    p.max_state_frame = at
    finished = False
    try:
        yield from p.start()
        if p.state_for(at) is None:
            raise CaptureError(
                "the replay keeps no state from before the game read the "
                "pointer, so the strings cannot be shown through it"
            )
        for i in range(first, end):
            src = table.slot(i)
            writes = [(slot + k, rom[src + k]) for k in range(table.size)]
            path = yield from p.shot(shot_at, writes, f"sweep_{i:04d}.png")
            yield (i, path)
        finished = True
    finally:
        p.close(wait=5.0 if finished else 0)
    return end
