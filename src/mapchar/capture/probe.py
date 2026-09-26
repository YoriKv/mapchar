"""The probe client: drives the probe server in a headless emulator.

The server replays a capture once, keeping a savestate every few frames and
the reference output — the writes to an observed range from the text's first
frame to its last, or a memory at a chosen frame; then each probe loads the
latest state before a given frame, writes ROM bytes, runs, undoes the writes,
and reports the output's difference and, when asked, a reader's first reads.

A probe's cost is the frames it runs, so it starts at the read in question and
stops at the last output compared. A change can leave the emulator making no
progress: a probe not answered by a deadline its frames set counts as *no
answer*, the server is restarted, and the work goes on without it.
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


def reader_cpu(console: Console, pc: int) -> str:
    """The processor a logged PC belongs to."""
    if console.lua == "snes":
        return "gsu" if pc >= 0x1000000 else "snes"
    return console.lua


@dataclass
class Answer:
    same: bool
    """The observed output is the reference's."""
    position: int = 0
    """Where the output first differs, 1-based among the values written."""
    k0: int = 0
    """The reference position the probe's run starts at."""
    values: list[int] = field(default_factory=list)
    """Every value written to the observed range in the probe's run."""
    reads: list[int] = field(default_factory=list)
    """The watched reader's first reads, as it reported them."""
    vram: tuple[int, int, int, dict[int, int]] | None = None
    """``(bytes changed, lowest, highest, {address: value})`` of the watched
    memory against its reference, when one is watched."""


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
        self.vobs_frame = vobs[1] if vobs else 0
        self.n = 0
        self.seconds = 0.0
        self.no_answer = 0
        self.proc = None
        self.conn: Lines | None = None
        self.states: list[int] = []
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
        )
        return s

    def start(self) -> Step[None]:
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
        refw = (yield from self._line(60)).split()[1:]
        self.ref = []
        for w in refw:
            f, a, v = w.split(":")
            self.ref.append((int(f), int(a, 16), int(v, 16)))
        self.ref_values = [w[2] for w in self.ref]
        if self.vobs and self.vobs_frame != self.vobs[1]:
            yield from self.move_vobs(self.vobs_frame)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _line(self, seconds: float) -> Step[str]:
        line = yield from self.conn.readline(time.monotonic() + seconds, self.alive)
        if line.startswith("err "):
            raise CaptureError(f"the probe script failed: {line[4:]}")
        return line

    def kill(self) -> None:
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
        self.kill()
        yield from self.start()

    def close(self) -> None:
        """Ask the server to quit; one still busy is killed, never left."""
        if self.conn is not None:
            try:
                self.conn.send("quit")
            except CaptureError:
                pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self.kill()

    # -- probes

    def state_for(self, frame: int) -> int | None:
        """The latest state taken before anything of frame value ``frame``
        ran, 1-based."""
        best = None
        for i, f in enumerate(self.states):
            if f <= frame:
                best = i + 1
        return best

    def run(self, writes, first_frame: int, mode: str = "full") -> Step[Answer | None]:
        """One probe; None when it was not answered in time."""
        si = self.state_for(first_frame) or 1
        t0 = time.monotonic()
        self.n += 1
        ws = ",".join(f"{a:X}:{v:X}" for a, v in writes)
        end = max(self.last, self.pend, self.vobs_frame)
        seconds = 30 + max(0, end - self.states[si - 1]) / FRAMES_PER_SECOND
        self.conn.send(f"probe {self.n} {si} {mode} {ws}")
        try:
            line = (yield from self._line(seconds)).split()
        except (Timeout, Closed):
            self.no_answer += 1
            self.seconds += time.monotonic() - t0
            yield from self.restart()
            return None
        self.seconds += time.monotonic() - t0
        ans = Answer(same=False)
        if line and line[-1].startswith("r="):
            ans.reads = [int(x, 16) for x in line[-1][2:].split(",") if x]
            line = line[:-1]
        if line and line[-1].startswith("d="):
            n, lo, hi = (int(x) for x in line[-2][2:].split(","))
            d = {}
            for x in line[-1][2:].split(","):
                if x:
                    a, v = x.split(":")
                    d[int(a, 16)] = int(v, 16)
            ans.vram = (n, lo, hi, d)
            line = line[:-2]
        start = self.states[si - 1]
        ans.k0 = next(
            (i for i, w in enumerate(self.ref) if w[0] >= start), len(self.ref)
        )
        if len(line) < 3 or line[0] != "res":
            raise CaptureError(f"the probe server said {' '.join(line)!r}")
        if line[2] == "same":
            ans.same = True
            return ans
        ans.position = int(line[3])
        ans.values = (
            [int(x, 16) for x in line[5].split(".")]
            if len(line) > 5 and line[5]
            else []
        )
        return ans

    def test(self, writes, first_frame: int) -> Step[bool]:
        """Whether the writes change the observed output at all."""
        ans = yield from self.run(writes, first_frame, "first")
        return ans is not None and not ans.same

    def effect(
        self, writes, first_frame: int
    ) -> Step[tuple[list[int], list[int]] | None]:
        """None, or ``(reference values from the probe's start, values
        written)``. The whole answer is :attr:`answer`."""
        ans = yield from self.run(writes, first_frame, "full")
        self.answer = ans
        if ans is None or ans.same:
            return None
        return self.ref_values[ans.k0 :], ans.values

    def settle(self, lo: int, hi: int, from_frame: int) -> Step[int]:
        """The earliest frame after which the watched memory ``lo..hi`` stays
        as it is at the watched frame, in the unchanged run."""
        self.conn.send(f"settle {self.state_for(from_frame) or 1} {lo} {hi}")
        return int((yield from self._line(REFERENCE_SECONDS)).split()[1])

    def move_vobs(self, frame: int) -> Step[int]:
        """Compare the watched memory at this frame from now on."""
        self.vobs_frame = self.pend = frame
        self.conn.send(f"vobs {self.state_for(frame) or 1} {frame}")
        return int((yield from self._line(REFERENCE_SECONDS)).split()[1])

    def shot(self, frame: int, writes, name: str) -> Step[str]:
        """A screenshot at ``frame`` with the writes made, saved in the
        capture's folder as ``name``; its path."""
        ws = ",".join(f"{a:X}:{v:X}" for a, v in writes)
        self.conn.send(f"shot {self.state_for(frame - 1) or 1} {frame} {name} {ws}")
        yield from self._line(REFERENCE_SECONDS)
        return os.path.join(self.folder, name)
