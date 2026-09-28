"""A capture session: the recorder in the user's emulator, the captures it
hands over, and the work each one goes through.

Captures live in ``<project>.capture/``, one folder each: the moment (ring,
input, capture point, screenshot), the typed text, the evidence, and the
results; beside them, the setup the recorder is launched with. A capture's
state is *waiting* (for its text, or its turn), *replaying*, *finding*,
*tracing*, *done* or *failed* with its reason.

:meth:`Session.advance` does a bounded step of work and returns; the UI calls
it from a timer. Captures are traced one at a time, in the order their text
was given.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from mapchar.capture.combine import Combined, Sighting, combine
from mapchar.capture.consoles import Console
from mapchar.capture.emulator import RECORDER, Emulator, build_script
from mapchar.capture.evidence import (
    EVIDENCE,
    SCREEN,
    Evidence,
    Moment,
    ingest,
    replay,
)
from mapchar.capture.occurrence import Finding, check_text, find
from mapchar.capture.probe import ProbeServer
from mapchar.capture.protocol import WAIT, CaptureError, Closed, Lines, Listener, Step
from mapchar.capture.setup import Setup
from mapchar.capture.trace import Result, Tracer

WAITING = "waiting"
REPLAYING = "replaying"
FINDING = "finding"
TRACING = "tracing"
DONE = "done"
FAILED = "failed"
BUSY = (REPLAYING, FINDING, TRACING)

RING_FRAMES = 113
"""Frames between the recorder's savestates: 16 of them span 30 seconds."""

RING_KEEP = 16

INCOMING = "incoming"
META = "capture.json"


def capture_root(project_path: str | None, rom_path: str) -> str:
    """Where a project's captures live: beside the project, or beside the ROM
    when there is no project yet. Never inside either."""
    return (project_path or rom_path) + ".capture"


@dataclass
class Capture:
    id: str
    folder: str
    text: str = ""
    state: str = WAITING
    reason: str = ""
    status: str = ""
    progress: tuple[int, int] | None = None
    """Done and total of the step :attr:`status` names, when it is counted."""
    result: Result | None = None
    finding: Finding | None = None
    order: int = 0
    """When its text was given, for the queue."""
    before: str = ""
    after: str = ""
    """Confirm in game: the screenshots without and with the change."""

    @property
    def screenshot(self) -> str | None:
        path = os.path.join(self.folder, SCREEN)
        return path if os.path.exists(path) else None

    @property
    def needs_text(self) -> bool:
        return self.state == WAITING and not self.text

    def save(self) -> None:
        data = {
            "text": self.text,
            "state": self.state,
            "reason": self.reason,
            "order": self.order,
            "result": self.result.to_json() if self.result else None,
            "finding": self.finding.__dict__ if self.finding else None,
        }
        tmp = os.path.join(self.folder, META + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.folder, META))

    @classmethod
    def load(cls, folder: str) -> Capture:
        cap = cls(os.path.basename(folder), folder)
        try:
            with open(os.path.join(folder, META), encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError:
            return cap
        cap.text = data.get("text", "")
        cap.state = data.get("state", WAITING)
        cap.reason = data.get("reason", "")
        cap.order = data.get("order", 0)
        if data.get("result"):
            cap.result = Result.from_json(data["result"])
        if data.get("finding"):
            cap.finding = Finding(**data["finding"])
        if cap.state in BUSY:  # interrupted: its turn comes again
            cap.state = WAITING
        return cap


@dataclass
class Job:
    capture: Capture
    step: Step
    tracer: Tracer | None = None


@dataclass
class Session:
    root: str
    rom_path: str
    console: Console
    emulator: Emulator
    captures: list[Capture] = field(default_factory=list)
    on_change: Callable[[Capture], None] | None = None
    """Called when a capture arrives or changes state."""

    def __post_init__(self):
        with open(self.rom_path, "rb") as fh:
            self.file_bytes = fh.read()
        self.rom = self.console.image(self.file_bytes)
        self.job: Job | None = None
        self.listener: Listener | None = None
        self.recorder: Lines | None = None
        self.player = None
        self.messages: list[str] = []
        os.makedirs(os.path.join(self.root, INCOMING), exist_ok=True)
        self.setup = Setup.load(self.root)
        self._load()

    def _load(self) -> None:
        for name in sorted(os.listdir(self.root)):
            folder = os.path.join(self.root, name)
            if name != INCOMING and os.path.isdir(folder):
                if os.path.exists(os.path.join(folder, "moment.txt")):
                    self.captures.append(Capture.load(folder))
        self._recover()

    def _recover(self) -> None:
        """Take in the moments the recorder left in ``incoming/`` unannounced:
        mapchar was not listening when it said so. A moment's description is
        written last, so one with a description is whole."""
        incoming = os.path.join(self.root, INCOMING)
        for name in sorted(os.listdir(incoming)):
            id, ext = os.path.splitext(name)
            if ext != ".txt" or id.endswith("_input") or self.capture(id):
                continue
            try:
                self.add_moment(id, os.path.join(incoming, id))
            except (OSError, CaptureError) as e:
                self.messages.append(f"A pause left behind could not be read: {e}")

    def _changed(self, cap: Capture) -> None:
        cap.progress = None  # a new state starts uncounted
        cap.save()
        if self.on_change is not None:
            self.on_change(cap)

    def capture(self, id: str) -> Capture | None:
        return next((c for c in self.captures if c.id == id), None)

    # -- playing

    def set_setup(self, setup: Setup) -> None:
        """What the user gives before playing, kept for the next time."""
        self.setup = setup
        setup.save(self.root)

    def play(self) -> None:
        """Launch the emulator on the ROM with the recorder."""
        if self.playing:
            return
        self.listener = Listener()
        self.recorder = None
        prefix = time.strftime("%Y%m%d-%H%M%S-")
        script = os.path.join(self.root, "_recorder.lua")
        settings = {
            "port": self.listener.port,
            "out": self.emulator.native_path(os.path.join(self.root, INCOMING)),
            "prefix": prefix,
            "ring": RING_FRAMES,
            "keep": RING_KEEP,
            **self.setup.recorder(self.console),
        }
        with open(script, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(build_script(RECORDER, self.console, settings))
        self.player = self.emulator.launch(
            self.rom_path, RECORDER, script, os.path.join(self.root, "_recorder.out")
        )

    @property
    def playing(self) -> bool:
        return self.player is not None and self.player.poll() is None

    def stop_playing(self) -> None:
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        if self.player is not None and self.player.poll() is None:
            self.player.kill()
        self.player = None

    def _poll_recorder(self) -> None:
        if self.listener is None:
            return
        if self.recorder is None:
            self.recorder = self.listener.poll()
            if self.recorder is None:
                return
        while True:
            try:
                line = self.recorder.poll()
            except Closed:
                self.recorder = None
                return
            if line is None:
                return
            if line.startswith("moment "):
                self._arrive(line.split()[1])
            elif line == "early":
                self.messages.append(
                    "Paused too soon after starting: play on a few seconds first."
                )
            elif line.startswith("err "):
                self.messages.append(f"The recorder failed: {line[4:]}")

    def _arrive(self, id: str) -> Capture:
        return self.add_moment(id, os.path.join(self.root, INCOMING, id))

    def add_moment(self, id: str, files: str) -> Capture:
        """A moment, from its files' common prefix: the recorder's, or one
        recorded elsewhere."""
        folder = os.path.join(self.root, id)
        moment = ingest(files, folder)
        if moment.font and moment.text is None:
            self.messages.append(
                "The font was not read before this pause: check where it is in "
                "the capture setup."
            )
        cap = Capture(id, folder)
        self.captures.append(cap)
        self._changed(cap)
        return cap

    # -- the user's side

    def submit(self, cap: Capture, text: str) -> Finding | None:
        """Give a capture its text; what makes the text unsearchable, if
        anything, is said at once and the capture keeps waiting."""
        bad = check_text(text)
        if bad is not None:
            return bad
        cap.text = text
        cap.state, cap.reason, cap.status = WAITING, "", ""
        cap.result, cap.finding = None, None
        cap.order = max((c.order for c in self.captures), default=0) + 1
        self._changed(cap)
        return None

    def skip(self, cap: Capture) -> None:
        """Drop a capture and its files."""
        if self.job is not None and self.job.capture is cap:
            self.stop()
        self.captures.remove(cap)
        shutil.rmtree(cap.folder, ignore_errors=True)
        if self.on_change is not None:
            self.on_change(cap)

    def stop(self) -> None:
        """Stop the capture being traced; it can be given its turn again."""
        job = self.job
        if job is None:
            return
        self.job = None
        job.step.close()
        if job.tracer is not None:
            job.tracer.close()
        job.capture.state, job.capture.reason = FAILED, "stopped"
        job.capture.status = ""
        self._changed(job.capture)

    def retry(self, cap: Capture) -> None:
        if cap.text:
            self.submit(cap, cap.text)

    # -- the work

    def _next(self) -> Capture | None:
        queued = [c for c in self.captures if c.state == WAITING and c.text]
        return min(queued, key=lambda c: c.order) if queued else None

    def advance(self, budget: float = 0.03) -> bool:
        """Do up to ``budget`` seconds of work; whether any is left."""
        self._poll_recorder()
        end = time.monotonic() + budget
        while time.monotonic() < end:
            if self.job is None:
                cap = self._next()
                if cap is None:
                    return False
                self.job = Job(cap, self._process(cap))
            job = self.job
            try:
                y = next(job.step)
            except StopIteration:
                self.job = None
                continue
            except Exception as e:  # noqa: BLE001 - any failure fails the capture
                self.job = None
                if job.tracer is not None:
                    job.tracer.close()
                job.capture.state = FAILED
                job.capture.reason = str(e) or type(e).__name__
                job.capture.status = ""
                self._changed(job.capture)
                continue
            if job.tracer is not None:
                job.capture.status = job.tracer.status
                job.capture.progress = job.tracer.progress
            if y is WAIT:
                break
        return True

    @property
    def busy(self) -> bool:
        return self.job is not None or self._next() is not None

    def _process(self, cap: Capture) -> Step[None]:
        cap.state, cap.status = REPLAYING, "replaying the moment"
        self._changed(cap)
        moment = Moment.load(cap.folder)
        if not os.path.exists(os.path.join(cap.folder, EVIDENCE)):
            r = yield from replay(
                self.emulator, self.rom_path, self.console, cap.folder
            )
            if not r.match:
                os.remove(os.path.join(cap.folder, EVIDENCE))
                raise CaptureError(
                    "the replay did not reproduce the moment: its RAM and VRAM "
                    "differ at the pause"
                )
        cap.state, cap.status = FINDING, "finding the text"
        self._changed(cap)
        ev = yield from Evidence.load(cap.folder, self.console, moment.state(1).frame)
        occ, bad = yield from find(ev, cap.text)
        if occ is None:
            cap.state, cap.finding = FAILED, bad
            cap.reason = bad.message if bad else "not found"
            cap.status = ""
            self._changed(cap)
            return
        cap.state, cap.status = TRACING, "tracing"
        self._changed(cap)
        tracer = Tracer(
            self.emulator, self.rom_path, self.rom, self.console, cap.folder, ev, occ
        )
        self.job.tracer = tracer
        cap.result = yield from tracer.run()
        cap.state, cap.status = DONE, ""
        self._changed(cap)

    # -- together

    def sightings(self) -> list[Sighting]:
        return [
            Sighting(c.id, c.text, c.result)
            for c in sorted(self.captures, key=lambda c: c.order)
            if c.state == DONE and c.result is not None
        ]

    def combined(self, mappings: dict) -> Combined:
        return combine(self.sightings(), self.rom, self.console, mappings)

    # -- Confirm in game

    def confirm(
        self, cap: Capture, writes: list[tuple[int, int]]
    ) -> Step[tuple[str, str]]:
        """The capture's frame without and with ROM bytes changed, as two
        screenshots in its folder."""
        p = ProbeServer(
            self.emulator, self.rom_path, self.console, cap.folder, name="confirm"
        )
        try:
            yield from p.start()
            frame = Moment.load(cap.folder).frame
            before = yield from p.shot(frame, [], "confirm_before.png")
            after = yield from p.shot(frame, writes, "confirm_after.png")
        finally:
            p.close()
        cap.before, cap.after = before, after
        return before, after

    def close(self) -> None:
        """Stop the work and the connection to the recorder; the emulator
        being played is the user's and keeps running."""
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        if self.job is not None:
            job, self.job = self.job, None
            job.step.close()
            if job.tracer is not None:
                job.tracer.close()
