"""A capture's moment, and the evidence its replay records.

A **moment** is what the recorder hands over at a pause: the ring of
savestates (after any the font breakpoint pinned), the input since the oldest,
the capture point (the frame, the poll, the master clock) with its RAM and
VRAM hash, the latest text's first and last font read, and a screenshot. The
**replay** runs it again headless from the oldest state, checks the hash — a
mismatch fails the capture — and on the way records every ROM data read with
its reading PC, every RAM write with its writing PC, every RAM at the capture
point, and which ROM bytes the replay read or executed. The log is read once
into a list of events. Between a pinned state and the ring lies a **gap**,
where the replay logs nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field

from mapchar.capture.consoles import Console
from mapchar.capture.emulator import REPLAY, Emulator, build_script
from mapchar.capture.protocol import CaptureError, Step, wait_process

MOMENT = "moment.txt"
INPUT = "input.txt"
SCREEN = "screen.png"
EVIDENCE = "evidence.log"

REPLAY_SECONDS = 900
"""The longest a replay may take; a replay is 16 to 30 seconds of play, run
several times faster than that — more after pinned states, but without its
log."""

GAP_TAIL = 60
"""Frames past the text's last font read that are still logged."""

Event = tuple[str, int, int, int, int]
"""``(kind, address, value, frame, pc)``: kind ``E`` is a ROM data read, its
address a ROM offset; ``W`` a RAM write, its address the bus's."""


@dataclass(frozen=True)
class State:
    index: int
    frame: int
    poll: int
    pinned: bool = False
    """Kept from before the latest text, where the ring no longer reaches."""


@dataclass(frozen=True)
class Moment:
    frame: int
    poll: int
    hash: str
    states: tuple[State, ...]
    clock: int | None = None
    """The master clock of the pause, which falls inside the frame after
    :attr:`frame`; None when the capture point is that frame's end."""
    font: bool = False
    """Whether the recorder watched a font."""
    text: tuple[int, int] | None = None
    """The frames of the latest text's first and last font read."""

    @classmethod
    def parse(cls, text: str) -> Moment:
        head = re.search(
            r"capture frame=(\d+) poll=(\d+)(?: clock=(\d+))? hash=(\S+)", text
        )
        if not head:
            raise CaptureError("the moment's description is unreadable")
        states = tuple(
            State(int(i), int(f), int(p), bool(pin))
            for i, f, p, pin in re.findall(
                r"state (\d+) frame=(\d+) poll=(\d+)( pinned)?", text
            )
        )
        clock = int(head.group(3)) if head.group(3) else None
        t = re.search(r"^text first=(\d+) last=(\d+)", text, re.M)
        return cls(
            int(head.group(1)),
            int(head.group(2)),
            head.group(4),
            states,
            clock,
            bool(re.search(r"^font watched", text, re.M)),
            (int(t.group(1)), int(t.group(2))) if t else None,
        )

    @property
    def gap(self) -> tuple[int, int] | None:
        """The frames between the text and the ring, logged by nothing: from
        :data:`GAP_TAIL` past the text's last font read to the ring's oldest
        state. None when there is no pinned state, or nothing between."""
        ring = [s for s in self.states if not s.pinned]
        if self.text is None or not ring or len(ring) == len(self.states):
            return None
        lo, hi = self.text[1] + GAP_TAIL, ring[0].frame
        return (lo, hi) if lo < hi else None

    @classmethod
    def load(cls, folder: str) -> Moment:
        with open(os.path.join(folder, MOMENT), encoding="utf-8") as fh:
            return cls.parse(fh.read())

    def state(self, index: int = 1) -> State:
        for s in self.states:
            if s.index == index:
                return s
        raise CaptureError(f"the moment has no state {index}")


def state_path(folder: str, index: int) -> str:
    return os.path.join(folder, f"s{index:02d}.mss")


def ingest(prefix: str, folder: str) -> Moment:
    """Move the recorder's files for one moment (``<prefix>.txt``,
    ``<prefix>_sNN.mss``, ``<prefix>_input.txt``, ``<prefix>.png``) into a
    capture folder. Nothing is moved unless every file it needs is there."""
    with open(prefix + ".txt", encoding="utf-8") as fh:
        moment = Moment.parse(fh.read())
    needed = [f"{prefix}_s{s.index:02d}.mss" for s in moment.states]
    missing = [p for p in [*needed, prefix + "_input.txt"] if not os.path.exists(p)]
    if missing:
        raise CaptureError(f"the moment is missing {os.path.basename(missing[0])}")
    os.makedirs(folder, exist_ok=True)
    shutil.move(prefix + ".txt", os.path.join(folder, MOMENT))
    for s in moment.states:
        shutil.move(f"{prefix}_s{s.index:02d}.mss", state_path(folder, s.index))
    shutil.move(prefix + "_input.txt", os.path.join(folder, INPUT))
    if os.path.exists(prefix + ".png"):
        shutil.move(prefix + ".png", os.path.join(folder, SCREEN))
    return moment


def script_settings(moment: Moment, folder: str, emulator: Emulator, index=1) -> dict:
    """What every headless script needs to replay a moment."""
    s = moment.state(index)
    return {
        "statePath": emulator.native_path(state_path(folder, s.index)),
        "stateFrame": s.frame,
        "statePoll": s.poll,
        "input": emulator.native_path(os.path.join(folder, INPUT)),
        "frame": moment.frame,
        "hash": moment.hash,
        "gap": list(moment.gap) if moment.gap else None,
    }


@dataclass(frozen=True)
class ReplayResult:
    match: bool
    hash: str
    events: int
    frame: int


def replay(
    emulator: Emulator,
    rom: str,
    console: Console,
    folder: str,
    *,
    evidence: bool = True,
    seconds: float = REPLAY_SECONDS,
) -> Step[ReplayResult]:
    """Replay the moment in ``folder`` headless, recording its evidence."""
    moment = Moment.load(folder)
    settings = script_settings(moment, folder, emulator)
    settings["clock"] = moment.clock
    if evidence:
        settings["evidence"] = emulator.native_path(os.path.join(folder, EVIDENCE))
    script = os.path.join(folder, "_replay.lua")
    with open(script, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build_script(REPLAY, console, settings))
    log = os.path.join(folder, "_replay.out")
    proc = emulator.launch(rom, REPLAY, script, log)
    out = yield from watch(proc, log, time.monotonic() + seconds)
    m = re.search(
        r"replay frame=(\d+) poll=\d+ hash=(\S+) match=(\d) events=(\d+)", out
    )
    if not m:
        err = re.search(r"^! (.*)$", out, re.M)
        raise CaptureError(
            "the replay did not finish" + (f": {err.group(1)}" if err else "")
        )
    return ReplayResult(m.group(3) == "1", m.group(2), int(m.group(4)), int(m.group(1)))


def watch(proc, log: str, deadline: float) -> Step[str]:
    """Wait for a headless run to end, and its output. A script stopped by an
    error leaves the emulator running: the error in its output ends the wait,
    and the emulator."""
    seen = 0
    while proc.poll() is None:
        try:
            with open(log, "rb") as fh:
                fh.seek(seen)
                chunk = fh.read()
        except OSError:  # not written yet
            chunk = b""
        if re.search(rb"^! ", chunk, re.M):
            proc.kill()
            proc.wait(timeout=10)
            break
        seen += len(chunk)
        yield from wait_process(
            proc, min(deadline, time.monotonic() + 0.25), quiet=True
        )
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait(timeout=10)
            raise CaptureError("the emulator did not finish in time")
    with open(log, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _chunks(text: str, start: int):
    """The log as ``(frame, lines)`` per frame; lines before the first ``F``
    line carry ``start``."""
    parts = text.split("\nF ")
    for n, part in enumerate(parts):
        lines = part.split("\n")
        if n:
            yield int(lines[0]), lines[1:]
        elif lines[0][:2] == "F ":
            yield int(lines[0][2:]), lines[1:]
        else:
            yield start, lines


@dataclass
class Evidence:
    folder: str
    console: Console
    events: list[Event] = field(default_factory=list)
    start: int = 0
    _touched: bytes | None | bool = None

    @classmethod
    def load(cls, folder: str, console: Console, start: int) -> Step[Evidence]:
        """Read the replay's log: each distinct line parsed once."""
        ev = cls(folder, console, start=start)
        to_rom = console.to_rom
        with open(os.path.join(folder, EVIDENCE), encoding="ascii") as fh:
            text = fh.read()
        yield
        parsed: dict[str, tuple | None] = {}
        events = ev.events
        n = 0
        for frame, lines in _chunks(text, start):
            for u in set(lines).difference(parsed):
                t = u[:1]
                if t == "E":
                    _, pc, a, v = u.split()
                    parsed[u] = ("E", to_rom(int(a, 16)), int(v, 16), int(pc, 16))
                elif t == "W":
                    _, pc, a, v = u.split()
                    parsed[u] = ("W", int(a, 16), int(v, 16), int(pc, 16))
                else:
                    parsed[u] = None
            events += [
                (c[0], c[1], c[2], frame, c[3])
                for c in map(parsed.__getitem__, lines)
                if c
            ]
            n += len(lines)
            if n > 200000:
                n = 0
                yield
        return ev

    @classmethod
    def of(cls, folder: str, console: Console, events: list[Event]) -> Evidence:
        """Evidence built in memory, for tests."""
        return cls(folder, console, events, events[0][3] if events else 0)

    def ram(self, name: str) -> bytes | None:
        """A memory as it was at the capture point."""
        try:
            with open(os.path.join(self.folder, f"{EVIDENCE}.{name}"), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def touched(self, offset: int) -> bool:
        """Whether this replay read or executed a ROM byte; True when unknown."""
        if self._touched is None:
            try:
                with open(os.path.join(self.folder, f"{EVIDENCE}.acc"), "rb") as fh:
                    self._touched = fh.read()
            except OSError:
                self._touched = False
        if self._touched is False:
            return True
        return offset < len(self._touched) and self._touched[offset] != 0
