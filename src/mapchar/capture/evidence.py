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
from mapchar.capture.protocol import CaptureError, Step, Timeout, wait_process

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
address a ROM offset; ``W`` a RAM write, its address the bus's in the one
spelling :meth:`~mapchar.capture.consoles.Console.canon` gives each RAM
byte."""


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
    err = re.search(r"^! (.*)$", out, re.M)
    if err:
        # A callback that failed left out what it would have logged.
        raise CaptureError(f"the replay's script failed: {err.group(1)}")
    if not m:
        raise CaptureError("the replay did not finish")
    return ReplayResult(m.group(3) == "1", m.group(2), int(m.group(4)), int(m.group(1)))


def end_process(proc) -> None:
    """Kill a process if it still runs, and reap it; never raises."""
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001 - it is going regardless
        pass


def watch(proc, log: str, deadline: float) -> Step[str]:
    """Wait for a headless run to end, and its output. A script stopped by an
    error leaves the emulator running: the error in its output ends the wait,
    and the emulator. However the wait ends — done, failed, or the step
    closed by Stop — the emulator does not outlive it."""
    seen, line = 0, b""
    try:
        while proc.poll() is None:
            try:
                with open(log, "rb") as fh:
                    fh.seek(seen)
                    chunk = fh.read()
            except OSError:  # not written yet
                chunk = b""
            seen += len(chunk)
            # From the start of the last line not yet whole: a marker may
            # arrive in two reads.
            text = line + chunk
            if re.search(rb"^! ", text, re.M):
                break
            line = text[text.rfind(b"\n") + 1 :]
            yield from wait_process(proc, min(deadline, time.monotonic() + 0.25))
            if time.monotonic() > deadline:
                raise Timeout("the emulator did not finish in time")
    finally:
        end_process(proc)
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


INDEX_CHUNK = 200000
"""Events indexed between two yields."""


def parse_line(u: str, console: Console) -> tuple | None:
    """One distinct log line as ``(kind, address, value, pc, bus)``; None for
    a frame mark, or a line the log was cut in the middle of. A read's
    address is its ROM offset — the emulator's where the line gives one, the
    profile's formula otherwise — and ``bus`` the address it was read at; a
    write's is its RAM byte's one spelling."""
    t = u[:1]
    if t != "E" and t != "W":
        return None
    f = u.split()
    if len(f) not in (4, 5):
        return None
    try:
        pc, a, v = int(f[1], 16), int(f[2], 16), int(f[3], 16)
        if t == "E":
            off = int(f[4], 16) if len(f) == 5 else console.to_rom(a)
            return ("E", off, v, pc, a)
        if len(f) == 5:
            mem, _, off = f[4].partition(":")
            if console.ram_bus is not None:
                try:
                    return ("W", console.ram_bus(mem, int(off, 16)), v, pc, a)
                except ValueError:
                    pass
        return ("W", console.canon(a), v, pc, a)
    except ValueError:
        return None


@dataclass
class Evidence:
    folder: str
    console: Console
    events: list[Event] = field(default_factory=list)
    start: int = 0
    bus_of: dict[int, int] = field(default_factory=dict)
    """ROM offset to the bus address a read of it was at, where the log gives
    both and they differ: the spelling a pointer to it would use."""
    bulk: list[tuple[int, int, int | None]] = field(default_factory=list)
    """``(page, from, to)``: a RAM page (bus address >> 8) whose writes the
    replay dropped from frame ``from`` to before ``to`` (None: to the end) —
    a buffer rewritten whole every frame."""
    _touched: bytes | None = None
    _touched_read: bool = False
    _reads_at: dict[int, list[int]] | None = None
    _reads_by: dict[int, list[int]] | None = None
    _frames: list[int] | None = None

    @classmethod
    def load(cls, folder: str, console: Console, start: int) -> Step[Evidence]:
        """Read the replay's log: each distinct line parsed once. Lines of
        the old form (no ROM offset) and a line cut short are both read."""
        ev = cls(folder, console, start=start)
        with open(
            os.path.join(folder, EVIDENCE), encoding="ascii", errors="replace"
        ) as fh:
            text = fh.read()
        if not text.endswith("\n"):  # cut in the middle of its last line
            text = text[: text.rfind("\n") + 1]
        yield
        parsed: dict[str, tuple | None] = {}
        events, bus_of = ev.events, ev.bus_of
        banning = "\nB " in text or text.startswith("B ")
        open_bans: dict[int, int] = {}
        n = 0
        for frame, lines in _chunks(text, start):
            ls = set(lines)
            if banning:
                for u in ls:
                    if u[:2] in ("B ", "U "):
                        page = int(u[2:], 16)
                        if u[0] == "B":
                            open_bans[page] = frame
                        elif page in open_bans:
                            ev.bulk.append((page, open_bans.pop(page), frame))
            for u in ls.difference(parsed):
                c = parsed[u] = parse_line(u, console)
                if c and c[0] == "E" and c[4] != c[1]:
                    bus_of[c[1]] = c[4]
            events += [
                (c[0], c[1], c[2], frame, c[3])
                for c in map(parsed.__getitem__, lines)
                if c
            ]
            n += len(lines)
            if n > INDEX_CHUNK:
                n = 0
                yield
        ev.bulk += [(page, f, None) for page, f in open_bans.items()]
        return ev

    @classmethod
    def of(cls, folder: str, console: Console, events: list[Event]) -> Evidence:
        """Evidence built in memory, for tests."""
        return cls(folder, console, events, events[0][3] if events else 0)

    # -- the index

    def index(self) -> Step[None]:
        """Index the reads by ROM offset and by PC, and every event's frame,
        yielding as it goes; once."""
        if self._reads_at is not None:
            return
        at: dict[int, list[int]] = {}
        by: dict[int, list[int]] = {}
        frames: list[int] = []
        for i, e in enumerate(self.events):
            frames.append(e[3])
            if e[0] == "E":
                at.setdefault(e[1], []).append(i)
                by.setdefault(e[4], []).append(i)
            if i % INDEX_CHUNK == INDEX_CHUNK - 1:
                yield
        self._reads_at, self._reads_by, self._frames = at, by, frames

    def _indexed(self) -> None:
        if self._reads_at is None:
            for _ in self.index():
                pass

    @property
    def reads_at(self) -> dict[int, list[int]]:
        """ROM offset to the indices of the events that read it, in order."""
        self._indexed()
        return self._reads_at

    @property
    def reads_by(self) -> dict[int, list[int]]:
        """PC to the indices of the reads it made, in order."""
        self._indexed()
        return self._reads_by

    @property
    def frames(self) -> list[int]:
        """Every event's frame, in order."""
        self._indexed()
        return self._frames

    def first_read(self, offset: int, pc: int | None = None) -> int | None:
        """The index of the first read of a ROM byte, by ``pc`` if given."""
        for i in self.reads_at.get(offset, ()):
            if pc is None or self.events[i][4] == pc:
                return i
        return None

    # -- the capture point

    def ram(self, name: str) -> bytes | None:
        """A memory as it was at the capture point."""
        try:
            with open(os.path.join(self.folder, f"{EVIDENCE}.{name}"), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    @property
    def touched_known(self) -> bool:
        """Whether this replay's ROM access counts were saved, so
        :meth:`touched` says more than True."""
        self.touched(0)
        return self._touched is not None

    def touched(self, offset: int) -> bool:
        """Whether this replay read or executed a ROM byte; True when unknown."""
        if not self._touched_read:
            self._touched_read = True
            try:
                with open(os.path.join(self.folder, f"{EVIDENCE}.acc"), "rb") as fh:
                    self._touched = fh.read()
            except OSError:
                self._touched = None
        if self._touched is None:
            return True
        return offset < len(self._touched) and self._touched[offset] != 0
