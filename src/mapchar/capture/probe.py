"""The probe client: drives the probe server in a headless emulator.

The server replays a capture once, keeping a savestate every few frames and
the reference output — the writes to an observed range from the text's first
frame to its last, or a memory at a chosen frame; then each probe loads the
latest state before a given frame, changes ROM bytes (or what reads of a byte
return), runs, undoes the changes, and reports the output's difference and,
when asked, a reader's first reads.

A probe's cost is the frames it runs, so it starts at the read in question and
stops at the last output compared. A change can leave the emulator making no
progress: a probe not answered by a deadline its frames set counts as *no
answer*, the server is restarted, and the work goes on without it. A server
that died between two requests is restarted and asked again.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from mapchar.capture.consoles import Console
from mapchar.capture.emulator import PROBE, Emulator, build_script
from mapchar.capture.evidence import Moment, script_settings
from mapchar.capture.protocol import (
    CaptureError,
    Closed,
    Lines,
    Listener,
    Step,
    Timeout,
)

START_SECONDS = 60
"""How long the server's script may take to connect."""

REFERENCE_SECONDS = 900
"""How long the reference replay may take."""

FRAMES_PER_SECOND = 20
"""The slowest a probe is expected to run; its deadline follows from it."""

PROBE_SECONDS = 30
"""A probe's deadline before the frames it runs are counted."""

STABLE_FRAMES = 8
"""Frames the text's VRAM must hold still before a probe compares it."""

STABLE_DEADLINE = 600
"""Frames after the changed byte's first read a VRAM probe compares at the
latest, still or not."""


STALLS_IN_A_ROW = 3
"""Stalls of the probe script, one probe after another, that end the tracing
as an error: a stall that comes back every time is not the machine's."""


class Stall(Timeout):
    """The probe script's callback ran past Mesen's one-second watchdog."""


class _Unsent(Closed):
    """The server was gone before a request went out."""


def reader_cpu(console: Console, pc: int) -> str:
    """The processor a logged PC belongs to."""
    if console.lua == "snes":
        if pc >= 0x2000000 and "sa1" in console.readers:
            return "sa1"
        return "gsu" if pc >= 0x1000000 else "snes"
    return console.cpu


@dataclass(frozen=True)
class ReadSub:
    """What reads of one byte return during a probe: ``value`` instead of
    the byte, on every read, only on those by ``pc``, or only on the
    ``nth`` of those (1-based, counted from the probe's savestate); with
    ``after``, only between the probe's ``after``-th write to the byte and
    the next — what reads return of one value written."""

    memory: str
    """The memory type the address is in: the ROM's, a RAM's."""
    address: int
    value: int
    pc: int | None = None
    nth: int | None = None
    after: int | None = None

    def spell(self) -> str:
        tail = [
            "" if self.pc is None else f"{self.pc:X}",
            "" if self.nth is None else str(self.nth),
            "" if self.after is None else str(self.after),
        ]
        while tail and not tail[-1]:
            tail.pop()
        return ":".join(
            ["r", self.memory, f"{self.address:X}", f"{self.value:X}", *tail]
        )


def changes(writes, reads=()) -> str:
    """A probe's changes as the server takes them."""
    return ",".join([f"{a:X}:{v:X}" for a, v in writes] + [r.spell() for r in reads])


@dataclass
class Answer:
    same: bool
    """The observed output is the reference's."""
    position: int = 0
    """Where the output first differs, 1-based among the values written."""
    k0: int = 0
    """The reference position the probe's run starts at."""
    values: list[int] = field(default_factory=list)
    """Every value written to the observed range in the probe's run — cut
    once past the reference's end, when :attr:`count` says how many."""
    reads: list[int] = field(default_factory=list)
    """The watched reader's first reads, at the addresses it read."""
    vram: tuple[int, int, int, dict[int, int]] | None = None
    """``(bytes changed, lowest, highest, {address: value})`` of the watched
    memory against its reference, when one is watched."""
    offsets: list[int] = field(default_factory=list)
    """:attr:`reads` as ROM offsets: the emulator's, else the profile's."""
    settled: int | None = None
    """With a VRAM window: the frame the text's VRAM held still at."""
    count: int = 0
    """How many values were written in all."""


class ProbeServer:
    def __init__(
        self,
        emulator: Emulator,
        rom: str,
        console: Console,
        folder: str,
        *,
        name: str = "probe",
        obs: tuple[str, int, int] | None = None,
        obs_from: int = 0,
        obs_to: int | None = None,
        robs: tuple | None = None,
        pend: int = 0,
        vobs: tuple[str, int] | None = None,
        spacing: int = 10,
        bulk: list | None = None,
    ):
        self.emulator, self.rom, self.console, self.folder = (
            emulator,
            rom,
            console,
            folder,
        )
        self.name = name
        self.moment = Moment.load(folder)
        self.obs, self.obs_from, self.obs_to = obs, obs_from, obs_to
        self.robs, self.pend, self.vobs, self.spacing = robs, pend, vobs, spacing
        self.bulk = bulk
        """``(page, from, to)`` of the observed range the replay dropped the
        writes of (:attr:`~mapchar.capture.evidence.Evidence.bulk`): dropped
        here too, so the two count alike."""
        self.vobs_frame = vobs[1] if vobs else 0
        self.n = 0
        self.seconds = 0.0
        self.no_answer = 0
        self.stalls = 0
        """Probes in a row that stalled the script."""
        self.proc = None
        self.conn: Lines | None = None
        self.log: str | None = None
        """The server's output file, once started."""
        self.answer: Answer | None = None
        """The whole answer to the latest :meth:`effect`."""
        self.awaiting = False
        """A request is out and its answer not in: quitting would wait."""
        self.win: tuple[int, int, int, int, int] | None = None
        """The VRAM window probes are compared in, once set."""
        self.rfrom: int | None = None
        """The ROM offset the reader's reads are counted from, once set."""
        self.control: tuple[list, int] | None = None
        """A change known to alter the output, and its frame: every run of
        the server after it is set checks it still does."""
        self.states: list[int] = []
        self.max_state_frame: int | None = None
        """When set, :meth:`state_for` picks no state taken after this frame:
        a run must start before something the game reads there. It holds
        across restarts."""
        self.ref: list[tuple[int, int, int]] = []
        self.ref_values: list[int] = []
        self.nref = 0
        self.last = 0

    # -- the server's life

    def _settings(self, port: int) -> dict:
        s = script_settings(self.moment, self.folder, self.emulator)
        s.update(
            port=port,
            dir=self.emulator.native_path(self.folder),
            spacing=self.spacing,
            obsFrom=self.obs_from,
            obsTo=self.obs_to,
            pend=self.pend,
            obs=list(self.obs) if self.obs else None,
            vobs=list(self.vobs) if self.vobs else None,
            robs=list(self.robs) if self.robs else None,
            bulk=[list(b) for b in self.bulk] if self.bulk else None,
        )
        return s

    def start(self) -> Step[None]:
        """Launch the server and wait for its reference; one that stalls or
        dies starting is tried once more."""
        for attempt in (0, 1):
            if attempt:
                self.kill()
            try:
                yield from self._start_once()
                return
            except (Timeout, Closed):
                if attempt:
                    raise

    def _start_once(self) -> Step[None]:
        listener = Listener()
        try:
            script = os.path.join(self.folder, f"_{self.name}.lua")
            with open(script, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(
                    build_script(PROBE, self.console, self._settings(listener.port))
                )
            self.log = os.path.join(self.folder, f"_{self.name}.out")
            self.proc = self.emulator.launch(self.rom, PROBE, script, self.log)
            self.conn = yield from listener.accept(
                time.monotonic() + START_SECONDS, self.alive
            )
        finally:
            listener.close()
        head = (yield from self._line(REFERENCE_SECONDS)).split()
        if not head or head[0] != "ref":
            raise CaptureError(f"the probe server said {' '.join(head)!r}")
        self.nref, self.last = int(head[1]), int(head[2])
        self.states = [int(x) for x in head[3].split(",")] if len(head) > 3 else []
        if not self.states:
            raise CaptureError("the probe server kept no savestate")
        refw = (yield from self._line(60)).split()[1:]
        self.ref = []
        for w in refw:
            f, a, v = w.split(":")
            self.ref.append((int(f), int(a, 16), int(v, 16)))
        self.ref_values = [w[2] for w in self.ref]
        if self.vobs and self.vobs_frame != self.vobs[1]:
            self.pend = self.vobs_frame
            yield from self._ask(
                f"vobs {self.state_for(self.vobs_frame) or 1} {self.vobs_frame}",
                REFERENCE_SECONDS,
            )
        if self.win is not None:
            yield from self._ask("vwin " + " ".join(map(str, self.win)), 60)
        if self.rfrom is not None:
            yield from self._ask(f"rfrom {self.rfrom:X}", 60)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _line(self, seconds: float) -> Step[str]:
        if self.conn is None:
            raise Closed("the probe server is not running")
        self.awaiting = True
        line = yield from self.conn.readline(time.monotonic() + seconds, self.alive)
        self.awaiting = False
        if line.startswith("err "):
            raise CaptureError(f"the probe script failed: {line[4:]}")
        if line.startswith("stall "):
            # The script's callback ran past its second: no answer, and a
            # fresh server for the next request.
            raise Stall(f"the probe script stalled: {line[6:]}")
        return line

    def _send(self, line: str) -> None:
        if self.conn is None:
            raise Closed("the probe server is not running")
        self.conn.send(line)

    def _ask(self, line: str, seconds: float) -> Step[str]:
        self._send(line)
        return (yield from self._line(seconds))

    def kill(self) -> None:
        self.awaiting = False
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 - it is going regardless
                pass

    def restart(self) -> Step[None]:
        """A fresh server, its positive control checked; one that stalls or
        dies starting, or during the control, is tried once more."""
        for attempt in (0, 1):
            self.kill()
            try:
                yield from self._start_once()
                if self.control is not None:
                    yield from self.check()
                return
            except (Timeout, Closed):
                if attempt:
                    raise

    def close(self, wait: float = 5.0) -> None:
        """Ask the server to quit and give it ``wait`` seconds; one still
        busy — mid-request, or with ``wait`` 0 or less — is killed at once,
        never left."""
        if wait > 0 and not self.awaiting and self.conn is not None and self.alive():
            try:
                self.conn.send("quit")
            except CaptureError:
                pass
            else:
                try:
                    self.proc.wait(timeout=wait)
                except Exception:  # noqa: BLE001 - killed below
                    pass
        self.kill()

    def _retry(self, request: str, seconds: float) -> Step[str]:
        """A request that is not a probe: a server that has died is
        restarted, and asked once more."""
        try:
            return (yield from self._ask(request, seconds))
        except (Timeout, Closed):
            yield from self.restart()
        return (yield from self._ask(request, seconds))

    # -- probes

    def state_for(self, frame: int) -> int | None:
        """The latest state taken before anything of frame value ``frame``
        ran, 1-based."""
        if self.max_state_frame is not None:
            frame = min(frame, self.max_state_frame)
        best = None
        for i, f in enumerate(self.states):
            if f <= frame:
                best = i + 1
        return best

    def run(
        self, writes, first_frame: int, mode: str = "full", reads=()
    ) -> Step[Answer | None]:
        """One probe; None when it was not answered in time. ``reads`` are
        :class:`ReadSub` s, made along with the ROM writes."""
        t0 = time.monotonic()
        try:
            try:
                ans = yield from self._probe(writes, first_frame, mode, reads)
            except _Unsent:
                # The server had died before the probe was out: its change
                # never ran, so it is asked again, of a fresh server.
                yield from self.restart()
                ans = yield from self._probe(writes, first_frame, mode, reads)
        except (Timeout, Closed) as e:
            self.no_answer += 1
            self.seconds += time.monotonic() - t0
            self.stalls = self.stalls + 1 if isinstance(e, Stall) else 0
            if self.stalls >= STALLS_IN_A_ROW:
                raise CaptureError(f"{e}, on every probe") from e
            yield from self.restart()
            return None
        self.stalls = 0
        return ans

    def _probe(self, writes, first_frame: int, mode: str, reads=()) -> Step[Answer]:
        if not self.states:
            raise Closed("the probe server has not started")
        si = self.state_for(first_frame) or 1
        t0 = time.monotonic()
        self.n += 1
        n = self.n
        end = max(self.last, self.pend, self.vobs_frame)
        if self.win is not None:
            end = max(end, first_frame + self.win[4])
        seconds = PROBE_SECONDS + max(0, end - self.states[si - 1]) / FRAMES_PER_SECOND
        deadline = t0 + seconds
        if not self.alive():
            raise _Unsent("the probe server has stopped")
        try:
            self._send(f"probe {n} {si} {mode} {changes(writes, reads)}")
        except Closed as e:
            raise _Unsent(str(e)) from e
        while True:
            words = (yield from self._line(deadline - time.monotonic())).split()
            # A late answer to an earlier probe, or another request's: not this.
            if words[:1] == ["res"] and len(words) > 1 and words[1] != str(n):
                continue
            if words[:1] and words[0] != "res":
                continue
            break
        self.seconds += time.monotonic() - t0
        return self._parse(words, si)

    def _parse(self, words: list[str], si: int) -> Answer:
        extra: dict[str, str] = {}
        while words and words[-1][:2] in ("r=", "d=", "v=", "s=", "w=", "n="):
            k, _, v = words.pop().partition("=")
            extra[k] = v
        if extra.get("w") == "0":
            raise CaptureError(
                "the emulator does not let ROM bytes be changed for this game, "
                "so nothing can be traced"
            )
        if len(words) < 3 or words[0] != "res":
            raise CaptureError(f"the probe server said {' '.join(words)!r}")
        ans = Answer(same=False)
        for x in extra.get("r", "").split(","):
            if not x:
                continue
            bus, _, off = x.partition("/")
            ans.reads.append(int(bus, 16))
            ans.offsets.append(
                int(off, 16) if off else self.console.to_rom(int(bus, 16))
            )
        if "v" in extra:
            n, lo, hi = (int(x) for x in extra["v"].split(","))
            d = {}
            for x in extra.get("d", "").split(","):
                if x:
                    a, v = x.split(":")
                    d[int(a, 16)] = int(v, 16)
            ans.vram = (n, lo, hi, d)
        if "s" in extra:
            ans.settled = int(extra["s"])
        start = self.states[si - 1]
        ans.k0 = next(
            (i for i, w in enumerate(self.ref) if w[0] >= start), len(self.ref)
        )
        if words[2] == "same":
            ans.same = True
            return ans
        ans.position = int(words[3])
        ans.values = (
            [int(x, 16) for x in words[5].split(".")]
            if len(words) > 5 and words[5]
            else []
        )
        ans.count = int(extra["n"]) if "n" in extra else len(ans.values)
        return ans

    def test(self, writes, first_frame: int, reads=()) -> Step[bool | None]:
        """Whether the writes change the observed output at all; None when
        the probe got no answer."""
        ans = yield from self.run(writes, first_frame, "first", reads)
        if ans is None:
            return None
        return not ans.same or bool(ans.vram and ans.vram[0])

    def effect(
        self, writes, first_frame: int, reads=()
    ) -> Step[tuple[list[int], list[int]] | None]:
        """None, or ``(reference values from the probe's start, values
        written)``. The whole answer is :attr:`answer`."""
        ans = yield from self.run(writes, first_frame, "full", reads)
        self.answer = ans
        if ans is None or ans.same:
            return None
        return self.ref_values[ans.k0 :], ans.values

    def check(self) -> Step[None]:
        """The positive control: :attr:`control` must still change the
        output. A server whose ROM changes no longer take is an error, not a
        run of *no sources*."""
        writes, frame = self.control
        ans = yield from self._probe(writes, frame, "full")
        if ans.same and not (ans.vram and ans.vram[0]):
            raise CaptureError(
                "a change that altered the text before no longer does: the "
                "emulator is not applying ROM changes"
            )

    def settle(self, lo: int, hi: int, from_frame: int) -> Step[int]:
        """The earliest frame after which the watched memory ``lo..hi`` stays
        as it is at the watched frame, in the unchanged run."""
        line = yield from self._retry(
            f"settle {self.state_for(from_frame) or 1} {lo} {hi}", REFERENCE_SECONDS
        )
        return int(line.split()[1])

    def move_vobs(self, frame: int) -> Step[int]:
        """Compare the watched memory at this frame from now on."""
        self.vobs_frame = self.pend = frame
        line = yield from self._retry(
            f"vobs {self.state_for(frame) or 1} {frame}", REFERENCE_SECONDS
        )
        return int(line.split()[1])

    def window(
        self,
        lo: int,
        hi: int,
        stable: int = STABLE_FRAMES,
        min_frames: int = 0,
        max_frames: int = STABLE_DEADLINE,
    ) -> Step[None]:
        """Compare the watched memory within ``lo..hi`` only, and by event:
        once it has held still ``stable`` frames, at least ``min_frames``
        after the changed byte's first read and at most ``max_frames``."""
        self.win = (lo, hi, stable, min_frames, max(max_frames, min_frames))
        yield from self._retry("vwin " + " ".join(map(str, self.win)), 60)

    def reads_from(self, offset: int) -> Step[None]:
        """Report the reader's reads from its first read of ROM offset
        ``offset`` on, rather than from the first in its range."""
        self.rfrom = offset
        yield from self._retry(f"rfrom {offset:X}", 60)

    def shot(self, frame: int, writes, name: str, reads=()) -> Step[str]:
        """A screenshot at ``frame`` with the writes made, saved in the
        capture's folder as ``name``; its path."""
        yield from self._retry(
            f"shot {self.state_for(frame - 1) or 1} {frame} {name} "
            + changes(writes, reads),
            REFERENCE_SECONDS,
        )
        return os.path.join(self.folder, name)
