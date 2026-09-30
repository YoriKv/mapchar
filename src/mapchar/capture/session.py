"""A capture session: the recorder in the user's emulator, the captures it
hands over, and the work each one goes through.

Captures live in ``<project>.capture/``, one folder each: the moment (ring,
input, capture point, screenshot), the typed text, the evidence, and the
results; beside them, the setup the recorder is launched with. A capture's
state is *waiting* (for its text, or its turn), *replaying*, *finding*,
*tracing*, *done* or *failed* with its reason.

:meth:`Session.advance` does a bounded step of work and returns; the UI calls
it from a timer. Captures are traced one at a time, in the order their text
was given. The same call reads the recorder's connection, watches the
emulator being played, and about once a second takes in the moments left in
``incoming/`` unannounced.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from mapchar.capture import tablesweep
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
from mapchar.capture.probe import START_SECONDS, ProbeServer
from mapchar.capture.protocol import WAIT, CaptureError, Closed, Lines, Listener, Step
from mapchar.capture.setup import Setup, write_json_atomic
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
REJECTED = "rejected"
"""Inside ``incoming/``: the moments that could not be taken in."""
META = "capture.json"

UNREADABLE = "its saved result could not be read"
"""A capture's reason when its ``capture.json`` does not read."""

ID = re.compile(r"\w[\w.-]*")
"""A moment id the recorder may name: a file name, never a path."""

HANDOFF_SECONDS = 10.0
"""An emulator that exits this soon after launch may have handed the game to
a window already open."""

RECOVER_SECONDS = 1.0
"""How often ``incoming/`` is looked through."""

SETTLE_SECONDS = 1.0
"""A moment's description younger than this may still be being written."""

ABANDON_SECONDS = 300.0
"""A moment in ``incoming/`` that has not read for this long is set aside."""

MESSAGE_SECONDS = 120.0
"""How long a message is shown."""

MESSAGES_KEPT = 50

_PART = re.compile(r"^(.*?)(?:_s\d+\.mss|_input\.txt|\.txt|\.png)$")
"""A file the recorder leaves for a moment, and the moment's id."""


def capture_root(project_path: str | None, rom_path: str) -> str:
    """Where a project's captures live: beside the project, or beside the ROM
    when there is no project yet. Never inside either."""
    return (project_path or rom_path) + ".capture"


def open_root(project_path: str | None, rom_path: str) -> str:
    """The captures' folder to open: the project's, or — while the project's
    has none — the one made beside the ROM before the project was saved."""
    root = capture_root(project_path, rom_path)
    if project_path and not os.path.isdir(root):
        rom_side = capture_root(None, rom_path)
        if os.path.isdir(rom_side):
            return rom_side
    return root


def close_now(server) -> None:
    """Close a probe server or a tracer without waiting for its emulator to
    quit: it is killed."""
    server.close(wait=0)


def drop_evidence(folder: str) -> None:
    """Remove a replay's evidence — the log and every file beside it."""
    for path in [os.path.join(folder, EVIDENCE), *glob.glob(_evidence_glob(folder))]:
        try:
            os.remove(path)
        except OSError:
            pass


def _evidence_glob(folder: str) -> str:
    return os.path.join(glob.escape(folder), glob.escape(EVIDENCE) + ".*")


def tidy(folder: str) -> None:
    """Remove the scripts and outputs of a finished capture's launches."""
    for pattern in ("_*.lua", "_*.out"):
        for path in glob.glob(os.path.join(glob.escape(folder), pattern)):
            try:
                os.remove(path)
            except OSError:
                pass


def _finding(d: dict) -> Finding:
    """A finding from its saved fields, those this version knows."""
    return Finding(**{k: v for k, v in d.items() if k in Finding.__dataclass_fields__})


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
    replayed: bool = False
    """Its evidence is whole: the replay finished and reproduced the moment."""
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
        write_json_atomic(
            os.path.join(self.folder, META),
            {
                "text": self.text,
                "state": self.state,
                "reason": self.reason,
                "order": self.order,
                "replayed": self.replayed,
                "result": self.result.to_json() if self.result else None,
                "finding": self.finding.__dict__ if self.finding else None,
            },
        )

    @classmethod
    def load(cls, folder: str) -> Capture:
        """The capture in ``folder``. One whose saved result does not read is
        kept, failed, with its text when that much read, so it can be tried
        again."""
        cap = cls(os.path.basename(folder), folder)
        try:
            with open(os.path.join(folder, META), encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return cap  # its text was never given
        except (OSError, ValueError):
            cap.state, cap.reason = FAILED, UNREADABLE
            return cap
        try:
            text = data.get("text", "")
            cap.text = text if isinstance(text, str) else ""
            cap.state = str(data.get("state", WAITING))
            cap.reason = str(data.get("reason", ""))
            cap.order = int(data.get("order", 0))
            cap.replayed = bool(data.get("replayed", False))
            if data.get("result"):
                cap.result = Result.from_json(data["result"])
            if data.get("finding"):
                cap.finding = _finding(data["finding"])
        except (ValueError, TypeError, KeyError, AttributeError):
            cap.state, cap.reason = FAILED, UNREADABLE
            cap.result = cap.finding = None
            return cap
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
    emulator: Emulator | None
    """None until the UI finds it: work waits for it, viewing does not."""
    captures: list[Capture] = field(default_factory=list)
    on_change: Callable[[Capture], None] | None = None
    """Called when a capture arrives or changes state."""
    on_message: Callable[[str], None] | None = None
    """Called with each message as it is said."""

    def __post_init__(self):
        with open(self.rom_path, "rb") as fh:
            self.file_bytes = fh.read()
        self.rom = self.console.image(self.file_bytes)
        self.job: Job | None = None
        self.listener: Listener | None = None
        self.recorder: Lines | None = None
        self.recorder_script: str | None = None
        """The script the latest Play launched the emulator with."""
        self.player = None
        self.played_at = 0.0
        self.exited_at: float | None = None
        self.hello = False
        """The recorder said hello on this Play's connection."""
        self.handed_over = False
        """The emulator launched handed the game to a window already open,
        whose recorder is the one connected."""
        self.closed = False
        """The emulator being played has closed since the last Play."""
        self._no_hello_said = False
        self._plays = 0
        self.messages: list[str] = []
        self._said: list[float] = []
        self._reported: set[str] = set()
        self._recovered_at = time.monotonic()
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
        written last, so one with a description is whole — once it is
        :data:`SETTLE_SECONDS` old, since a recorder, this session's or one
        left running, may still be writing it. What does not read is said
        once, and set aside in ``incoming/rejected/`` once it is old."""
        self._recovered_at = time.monotonic()
        incoming = os.path.join(self.root, INCOMING)
        try:
            names = os.listdir(incoming)
        except OSError:
            return
        groups: dict[str, list[str]] = defaultdict(list)
        for name in names:
            m = _PART.match(name)
            if m and os.path.isfile(os.path.join(incoming, name)):
                groups[m.group(1)].append(name)
        now = time.time()
        for gid in sorted(groups):
            files = groups[gid]
            try:
                age = now - max(
                    os.path.getmtime(os.path.join(incoming, f)) for f in files
                )
            except OSError:
                continue
            if gid + ".txt" in files:
                if age < SETTLE_SECONDS:
                    continue  # a recorder, ours or not, may be writing it
                try:
                    self.add_moment(gid, os.path.join(incoming, gid))
                    continue
                except (OSError, ValueError, CaptureError) as e:
                    problem = f"A pause left behind ({gid}) could not be read: {e}"
            else:
                problem = "A pause left behind was never finished: only " + ", ".join(
                    sorted(files)
                )
            if age >= ABANDON_SECONDS:
                self._set_aside(files)
                self._say(f"{problem}. It is set aside in {INCOMING}/{REJECTED}.")
            elif gid + ".txt" in files and gid not in self._reported:
                self._reported.add(gid)
                self._say(problem)

    def _set_aside(self, files: list[str]) -> None:
        incoming = os.path.join(self.root, INCOMING)
        rejected = os.path.join(incoming, REJECTED)
        try:
            os.makedirs(rejected, exist_ok=True)
            for f in files:
                os.replace(os.path.join(incoming, f), os.path.join(rejected, f))
        except OSError:
            pass

    def _changed(self, cap: Capture) -> None:
        cap.progress = None  # a new state starts uncounted
        cap.save()
        if self.on_change is not None:
            self.on_change(cap)

    def _say(self, text: str) -> None:
        self.messages.append(text)
        self._said.append(time.monotonic())
        del self.messages[:-MESSAGES_KEPT], self._said[:-MESSAGES_KEPT]
        if self.on_message is not None:
            self.on_message(text)

    def recent_messages(
        self, n: int = 3, seconds: float = MESSAGE_SECONDS
    ) -> list[str]:
        """The latest messages, those said in the last ``seconds``."""
        now = time.monotonic()
        said = zip(self.messages, self._said, strict=True)
        return [m for m, t in said if now - t <= seconds][-n:]

    def capture(self, id: str) -> Capture | None:
        return next((c for c in self.captures if c.id == id), None)

    # -- the captures' folder

    def can_rehome(self, root: str) -> bool:
        """Whether the captures can move to ``root``: nothing is there, and
        no emulator being played writes into this folder."""
        return root != self.root and not os.path.exists(root) and self.listener is None

    def rehome(self, root: str) -> bool:
        """Move the captures to ``root`` — the project's folder, once it is
        saved — when :meth:`can_rehome`. The folder is renamed, never copied:
        where it cannot be (another drive, a file in use), it stays and the
        session says so. Work under way starts again."""
        if not self.can_rehome(root):
            return False
        job = self._drop_job()
        try:
            os.rename(self.root, root)
        except OSError as e:
            self._say(f"The captures stay in {self.root}: {e}")
            return False
        else:
            self.root = root
            for c in self.captures:
                c.folder = os.path.join(root, c.id)
            return True
        finally:
            if job is not None:
                job.capture.state, job.capture.status = WAITING, ""
                self._changed(job.capture)

    # -- playing

    def set_setup(self, setup: Setup) -> None:
        """What the user gives before playing, kept for the next time."""
        self.setup = setup
        setup.save(self.root)

    def play(self) -> None:
        """Launch the emulator on the ROM with the recorder."""
        if self.playing:
            return
        if self.emulator is None:
            raise CaptureError("no emulator is set")
        self.stop_playing()
        self._plays += 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        # A script of its own each launch: an emulator window left open from
        # before, reloading its script when the file changes, never picks up
        # this one.
        name = f"_recorder-{stamp}-{self._plays}"
        for old in glob.glob(os.path.join(glob.escape(self.root), "_recorder*")):
            try:
                os.remove(old)
            except OSError:
                pass
        listener = Listener()
        try:
            script = os.path.join(self.root, name + ".lua")
            settings = {
                "port": listener.port,
                "out": self.emulator.native_path(os.path.join(self.root, INCOMING)),
                "prefix": stamp + "-",
                "ring": RING_FRAMES,
                "keep": RING_KEEP,
                **self.setup.recorder(self.console),
            }
            with open(script, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(build_script(RECORDER, self.console, settings))
            player = self.emulator.launch(
                self.rom_path, RECORDER, script, os.path.join(self.root, name + ".out")
            )
        except BaseException:
            listener.close()
            raise
        self.listener, self.player, self.recorder_script = listener, player, script
        self.played_at, self.exited_at = time.monotonic(), None
        self.hello = self.handed_over = self.closed = self._no_hello_said = False

    @property
    def playing(self) -> bool:
        if self.player is not None and self.player.poll() is None:
            return True
        return self.handed_over and self.recorder is not None

    @property
    def recorder_state(self) -> str:
        """``connected``, ``waiting`` (for the recorder to connect),
        ``closed`` (the emulator played has closed) or ``""``."""
        if self.listener is None:
            return "closed" if self.closed else ""
        return "connected" if self.recorder is not None else "waiting"

    def stop_playing(self) -> None:
        """Close the emulator being played and the connection to it; one the
        game was handed to is the user's, and only the connection closes."""
        self._disconnect()
        if self.player is not None and self.player.poll() is None:
            self.player.kill()
        self.player = None
        self.handed_over = False
        self._recover()

    def _disconnect(self) -> None:
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None
        if self.listener is not None:
            self.listener.close()
            self.listener = None

    def _closed(self) -> None:
        self._disconnect()
        self.player, self.handed_over, self.exited_at = None, False, None
        self.closed = True
        self._recover()
        self._say("The emulator closed.")

    def _watch_player(self) -> None:
        """Follow the emulator being played: whether its recorder started,
        whether it handed the game to a window already open, and when it
        closed."""
        if self.listener is None:
            return
        now = time.monotonic()
        if not self.hello and not self._no_hello_said:
            if now - self.played_at > START_SECONDS:
                self._no_hello_said = True
                self._say(
                    "The recorder script did not start in the emulator: nothing "
                    "is captured. Check the emulator's script window, and play "
                    "again."
                )
        if self.handed_over:
            if self.recorder is None:
                self._closed()
            return
        if self.player is None or self.player.poll() is None:
            return
        if self.exited_at is None:
            self.exited_at = now
        quick = self.exited_at - self.played_at <= HANDOFF_SECONDS
        if quick and self.recorder is not None:
            # Mesen reads its switches after it looks for a window already
            # open, and hands that window the game: the recorder runs there.
            self.handed_over = True
            self.player = None
            self._say(
                "The game opened in a Mesen window that was already open: play "
                "there. Close the other window first to play in a new one."
            )
            return
        if quick and now - self.played_at < START_SECONDS:
            return  # the recorder of a window handed the game may yet connect
        self._closed()

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
                self.recorder.close()
                self.recorder = None
                return
            if line is None:
                return
            parts = line.split(maxsplit=1)
            kind = parts[0] if parts else ""
            rest = parts[1] if len(parts) > 1 else ""
            if kind == "moment":
                self._moment_said(rest.strip())
            elif kind == "hello":
                self.hello = True
            elif line == "early":
                self._say(
                    "Paused too soon after starting: play on a few seconds first."
                )
            elif kind == "err":
                self._say(f"The recorder failed: {rest}")
            elif line.strip():
                # A line of a kind mapchar does not know is said, not dropped.
                self._say(f"The recorder said: {line.strip()}")

    def _moment_said(self, id: str) -> None:
        if not ID.fullmatch(id):
            self._say(f"The recorder named a pause mapchar cannot read: {id!r}")
            return
        try:
            self._arrive(id)
        except (OSError, ValueError, CaptureError) as e:
            self._say(f"A pause could not be taken in: {e}")

    def _arrive(self, id: str) -> Capture:
        prefix = os.path.join(self.root, INCOMING, id)
        cap = self.capture(id)
        if cap is not None and not os.path.exists(prefix + ".txt"):
            return cap  # taken in from incoming/ before it was announced
        return self.add_moment(id, prefix)

    def _free_id(self, id: str) -> str:
        def taken(cid: str) -> bool:
            return self.capture(cid) is not None or os.path.exists(
                os.path.join(self.root, cid)
            )

        if not taken(id):
            return id
        n = 2
        while taken(f"{id}-{n}"):
            n += 1
        return f"{id}-{n}"

    def add_moment(self, id: str, files: str) -> Capture:
        """A moment, from its files' common prefix: the recorder's, or one
        recorded elsewhere. An id a capture already has gets a number."""
        cid = self._free_id(id)
        folder = os.path.join(self.root, cid)
        moment = ingest(files, folder)
        if moment.font and moment.text is None:
            self._say(
                "The font was not read before this pause: check where it is in "
                "the capture setup."
            )
        cap = Capture(cid, folder)
        self.captures.append(cap)
        self._changed(cap)
        return cap

    # -- the user's side

    def submit(self, cap: Capture, text: str) -> Finding | None:
        """Give a capture its text; what makes the text unsearchable, if
        anything, is said at once and the capture keeps waiting. A capture
        being worked on starts again with the new text."""
        bad = check_text(text)
        if bad is not None:
            return bad
        if self.job is not None and self.job.capture is cap:
            self._drop_job()
        cap.text = text
        cap.state, cap.reason, cap.status = WAITING, "", ""
        cap.result, cap.finding = None, None
        cap.order = max((c.order for c in self.captures), default=0) + 1
        self._changed(cap)
        return None

    def skip(self, cap: Capture) -> None:
        """Drop a capture and its files."""
        if self.job is not None and self.job.capture is cap:
            self._drop_job()
        self.captures.remove(cap)
        shutil.rmtree(cap.folder, ignore_errors=True)
        if self.on_change is not None:
            self.on_change(cap)

    def stop(self) -> None:
        """Stop the capture being traced; it can be given its turn again."""
        job = self._drop_job()
        if job is not None:
            self._fail(job, "stopped")

    def retry(self, cap: Capture) -> None:
        if cap.text:
            self.submit(cap, cap.text)

    # -- the work

    def _drop_job(self) -> Job | None:
        """End the job under way at once, its emulators killed."""
        job, self.job = self.job, None
        if job is None:
            return None
        if job.tracer is not None:
            close_now(job.tracer)
        job.step.close()
        job.capture.status = ""
        return job

    def _fail(self, job: Job, reason: str) -> None:
        cap = job.capture
        cap.state, cap.reason, cap.status = FAILED, reason, ""
        self._changed(cap)

    def _next(self) -> Capture | None:
        queued = [c for c in self.captures if c.state == WAITING and c.text]
        return min(queued, key=lambda c: c.order) if queued else None

    def advance(self, budget: float = 0.03) -> bool:
        """Do up to ``budget`` seconds of work; whether any is left."""
        self._poll_recorder()
        self._watch_player()
        if time.monotonic() - self._recovered_at >= RECOVER_SECONDS:
            self._recover()
        if self.job is None:
            for c in self.captures:
                if c.state in BUSY:  # its job ended without saying so
                    c.state, c.status = WAITING, ""
                    self._changed(c)
        end = time.monotonic() + budget
        while time.monotonic() < end:
            if self.job is None:
                cap = self._next() if self.emulator is not None else None
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
                    close_now(job.tracer)
                self._fail(job, str(e) or type(e).__name__)
                continue
            if job.tracer is not None:
                job.capture.status = job.tracer.status
                job.capture.progress = job.tracer.progress
            if y is WAIT:
                break
        return True

    @property
    def busy(self) -> bool:
        return self.job is not None or (
            self.emulator is not None and self._next() is not None
        )

    @property
    def needs_emulator(self) -> bool:
        """Captures wait their turn, and no emulator is set to trace them."""
        return self.emulator is None and self._next() is not None

    def _process(self, cap: Capture) -> Step[None]:
        cap.state, cap.status = REPLAYING, "replaying the moment"
        self._changed(cap)
        moment = Moment.load(cap.folder)
        if not (cap.replayed and os.path.exists(os.path.join(cap.folder, EVIDENCE))):
            # Evidence is kept only once its replay has finished and matched:
            # the replay writes its log from its start.
            cap.replayed = False
            try:
                r = yield from replay(
                    self.emulator, self.rom_path, self.console, cap.folder
                )
            except BaseException:  # stopped (GeneratorExit) or failed
                drop_evidence(cap.folder)
                raise
            if not r.match:
                drop_evidence(cap.folder)
                raise CaptureError(
                    "the replay did not reproduce the moment: its RAM and VRAM "
                    "differ at the pause"
                )
            cap.replayed = True
            cap.save()
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
        tidy(cap.folder)

    # -- together

    def sightings(self) -> list[Sighting]:
        return [
            Sighting(c.id, c.text, c.result)
            for c in sorted(self.captures, key=lambda c: c.order)
            if c.state == DONE and c.result is not None
        ]

    def combined(self, mappings: dict) -> Combined:
        return combine(self.sightings(), self.rom, self.console, mappings)

    # -- in the game

    def confirm(
        self, cap: Capture, writes: list[tuple[int, int]]
    ) -> Step[tuple[str, str]]:
        """The capture's frame without and with ROM bytes changed, as two
        screenshots in its folder. Closed before its end, its emulator is
        killed."""
        p = ProbeServer(
            self.emulator, self.rom_path, self.console, cap.folder, name="confirm"
        )
        finished = False
        try:
            yield from p.start()
            frame = Moment.load(cap.folder).frame
            before = yield from p.shot(frame, [], "confirm_before.png")
            after = yield from p.shot(frame, writes, "confirm_after.png")
            finished = True
        finally:
            if finished:
                p.close()
            else:
                close_now(p)
        cap.before, cap.after = before, after
        return before, after

    def sweep(
        self,
        cap: Capture,
        table: tablesweep.Slots,
        first: int = 0,
        limit: int = tablesweep.MAX_STRINGS,
    ) -> Step[int]:
        """Every string of ``table`` in the capture's text box: see
        :func:`tablesweep.sweep`."""
        return tablesweep.sweep(
            self.emulator,
            self.rom_path,
            self.rom,
            self.console,
            cap.folder,
            cap.result,
            table,
            first,
            limit,
        )

    def close(self) -> None:
        """Stop the work and the connection to the recorder; the emulator
        being played is the user's and keeps running."""
        self._disconnect()
        self._drop_job()
