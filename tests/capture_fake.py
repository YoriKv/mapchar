"""A game and an emulator made up for the capture tests.

:class:`Game` is a tiny text engine over a 4 KiB ROM: a table of 2-byte
pointers to ``$FF``-terminated strings, one byte a character; showing message
*m* reads its pointer, then copies the string into RAM one byte a frame's
worth at a time. :class:`FakeEmulator` stands in for Mesen: launched in the
replay role it writes the evidence the replay script would, and in the probe
role it serves the probe script's line protocol over TCP, re-running the game
with the ROM bytes it is told to change.
"""

from __future__ import annotations

import os
import re
import socket
import threading

from mapchar.capture.consoles import Console

LETTERS = {
    **{chr(65 + i): i for i in range(26)},
    **{chr(97 + i): 0x40 + i for i in range(26)},
}
LETTERS.update({" ": 0x1F, ".": 0x1B, "!": 0x1A})
END = 0xFF
TABLE = 0x100
STRINGS = 0x200
POINTER_PC, READER_PC, WRITER_PC, NOISE_PC = 0x8200, 0x9000, 0x9010, 0x8100
BUFFER = 0x7E2000
MESSAGES = [
    "Hello there.",
    "Welcome to the Island of Tests!",
    "Every string ends here.",
    "Nothing else.",
]
CAPTURE_FRAME = 10

CONSOLE = Console(
    "test",
    "Test console",
    "test",
    "snes",
    0xFFFFFF,
    "snesPrgRom",
    ("snesWorkRam", "snesVideoRam"),
    ("snes",),
    lambda a: a,
    lambda off: [off],
    lambda bus: ("snesWorkRam", bus & 0x1FFFF),
    ("linear",),
    (2,),
    ram_bus=lambda memory, off: 0x7E0000 | off,
)


def encode(text: str) -> bytes:
    return bytes(LETTERS[c] for c in text)


def build_rom() -> bytes:
    rom = bytearray([0xEE] * 0x1000)
    at = STRINGS
    for n, msg in enumerate(MESSAGES):
        rom[TABLE + 2 * n : TABLE + 2 * n + 2] = at.to_bytes(2, "little")
        body = encode(msg) + bytes([END])
        rom[at : at + len(body)] = body
        at += len(body)
    return bytes(rom)


class Memory:
    """What a probe's reads see: the ROM with its bytes changed, RAM as the
    game wrote it, and the probe's read substitutes (``r:`` changes), counted
    as the probe script counts them."""

    def __init__(self, rom: bytes, subs=()):
        self.rom = rom
        self.ram: dict[int, int] = {}
        self.subs = [dict(s, n=0, writes=0) for s in subs]

    def read(self, pc: int, a: int, memory: str = "snesPrgRom") -> int:
        v = self.rom[a] if memory == "snesPrgRom" else self.ram.get(a, 0)
        for s in self.subs:
            if s["mem"] != memory or s["addr"] != a:
                continue
            if s["after"] is not None and s["writes"] != s["after"]:
                continue
            if s["pc"] is not None and s["pc"] != pc:
                continue
            s["n"] += 1
            if s["nth"] is None or s["n"] == s["nth"]:
                v = s["value"]
        return v

    def read_access(self, pc: int, a: int, width: int) -> int:
        """A GBA-style ROM read of ``width`` bytes at aligned ``a``, with the
        probe script's rule: a substitute counts every access from its
        byte's word start up to the byte, and changes the byte's lane."""
        v = int.from_bytes(self.rom[a : a + width], "little")
        for s in self.subs:
            if s["mem"] != "snesPrgRom" or not s["addr"] & ~3 <= a <= s["addr"]:
                continue
            if s["after"] is not None and s["writes"] != s["after"]:
                continue
            if s["pc"] is not None and s["pc"] != pc:
                continue
            s["n"] += 1
            if s["nth"] is not None and s["n"] != s["nth"]:
                continue
            shift = ((s["addr"] & 3) - (a & 3)) * 8
            v = (v & ~(0xFF << shift)) | ((s["value"] & 0xFF) << shift)
        return v

    def write(self, a: int, v: int, memory: str = "snesWorkRam") -> None:
        self.ram[a] = v
        for s in self.subs:
            if s["mem"] == memory and s["addr"] == a:
                s["writes"] += 1


class Game:
    """Shows message ``message`` at frame 3; the capture point is frame 10."""

    def __init__(self, rom: bytes, message: int = 1):
        self.rom = rom
        self.message = message

    def run(self, rom: bytes | None = None, subs=()) -> list[tuple]:
        """Every event as ``(kind, pc, address, value, frame)``; ``subs``
        the probe's read substitutes."""
        rom = self.rom if rom is None else rom
        m = Memory(rom, subs)
        ev = []
        for a in range(0x800, 0x810):  # something else the game reads each frame
            ev.append(("E", NOISE_PC, a, rom[a], 1))
        slot = TABLE + 2 * self.message
        lo, hi = m.read(POINTER_PC, slot), m.read(POINTER_PC, slot + 1)
        ev.append(("E", POINTER_PC, slot, lo, 3))
        ev.append(("E", POINTER_PC, slot + 1, hi, 3))
        at = lo | hi << 8
        ev += self.text(m, at)
        for a in range(0x800, 0x810):
            ev.append(("E", NOISE_PC, a, rom[a], 6))
        return ev

    def text(self, m: Memory, at: int) -> list[tuple]:
        """The string at ``at`` read and copied into the buffer."""
        ev = []
        for k in range(64):
            if at + k >= len(m.rom):
                break
            v = m.read(READER_PC, at + k)
            ev.append(("E", READER_PC, at + k, v, 3))
            if v == END:
                break
            ev.append(("W", WRITER_PC, BUFFER + k, v, 3))
        return ev

    def vram(self, events, frame: int | None = None) -> dict[int, int]:
        """The VRAM the events leave, up to ``frame``."""
        return {
            a: v
            for kind, _, a, v, f in events
            if kind == "V" and (frame is None or f <= frame)
        }

    def log(self) -> str:
        lines, frame = [], 0
        for kind, pc, a, v, f in self.run():
            while frame < f:
                frame += 1
                lines.append(f"F {frame}")
            lines.append(f"{kind} {pc:X} {a:X} {v:X}")
        while frame < CAPTURE_FRAME:
            frame += 1
            lines.append(f"F {frame}")
        return "\n".join(lines) + "\n"

    def ram(self) -> bytes:
        ram = bytearray(0x20000)
        for kind, _, a, v, _ in self.run():
            if kind == "W":
                ram[a & 0x1FFFF] = v
        return bytes(ram)


def write_moment(folder: str, extra: str = "") -> None:
    """A moment as the recorder leaves it: one state at frame 0, and ``extra``
    lines."""
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "moment.txt"), "w") as fh:
        fh.write(
            f"capture frame={CAPTURE_FRAME} poll=10 hash=CAFE\nstate 1 frame=0 poll=0\n"
            + extra
        )
    with open(os.path.join(folder, "s01.mss"), "wb") as fh:
        fh.write(b"state")
    with open(os.path.join(folder, "input.txt"), "w") as fh:
        fh.write("".join(f"{p} \n" for p in range(1, 11)))
    with open(os.path.join(folder, "screen.png"), "wb") as fh:
        fh.write(b"\x89PNG")


_LUA_STR = r"\[(?P<eq>=*)\[\n?(?P<body>.*?)\](?P=eq)\]"


def _cfg(script: str) -> str:
    """The generated ``CFG = ...`` of a script, however many lines it
    spans."""
    start = script.index("CFG = ")
    end = script.find("\n-- mapchar capture:", start)
    return script[start : end if end >= 0 else len(script)]


def _str(script: str, key: str) -> str | None:
    m = re.search(rf"\b{key} = {_LUA_STR}", script, re.S)
    return m.group("body") if m else None


def _num(script: str, key: str) -> int | None:
    m = re.search(rf"\b{key} = (-?\d+)", script)
    return int(m.group(1)) if m else None


def _list(script: str, key: str) -> list | None:
    m = re.search(rf"\b{key} = \{{ ((?:[^{{}}]|{_LUA_STR})*?) \}}", script, re.S)
    if not m:
        return None
    out = []
    pat = rf"{_LUA_STR}|(?P<num>-?\d+)|(?P<nil>nil)|(?P<bool>true|false)"
    for part in re.finditer(pat, m.group(1), re.S):
        if part.group("num") is not None:
            out.append(int(part.group("num")))
        elif part.group("nil"):
            out.append(None)
        elif part.group("bool"):
            out.append(part.group("bool") == "true")
        else:
            out.append(part.group("body"))
    return out


def _changes(text: str):
    """A probe's changes: ROM writes and read substitutes."""
    writes, subs = [], []
    for item in text.split(","):
        if not item:
            continue
        if item.startswith("r:"):
            f = item[2:].split(":") + [""] * 3
            subs.append(
                {
                    "mem": f[0],
                    "addr": int(f[1], 16),
                    "value": int(f[2], 16),
                    "pc": int(f[3], 16) if f[3] else None,
                    "nth": int(f[4]) if f[4] else None,
                    "after": int(f[5]) if f[5] else None,
                }
            )
        else:
            a, v = item.split(":")
            writes.append((int(a, 16), int(v, 16)))
    return writes, subs


class FakeProc:
    """What :func:`subprocess.Popen` returns, for a thread."""

    def __init__(self, target):
        self.returncode = None
        self.stopped = threading.Event()
        self.killed = False

        def run():
            try:
                target(self)
            finally:
                if self.returncode is None:
                    self.returncode = 0

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.stopped.set()
        self.thread.join(timeout=5)
        self.returncode = -9

    def wait(self, timeout=None):
        self.thread.join(timeout)
        if self.thread.is_alive():
            import subprocess

            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class FakeEmulator:
    id = "fake"
    name = "Fake"

    def __init__(
        self,
        game: Game,
        match: bool = True,
        split: bool = True,
        fault: str | None = None,
        replay_hang: bool = False,
        replay_error: str | None = None,
    ):
        self.game = game
        self.match = match
        self.split = split
        """Send every line in two pieces, as a socket may deliver them."""
        self.fault = fault
        """A probe server's fault: ``silent`` (never answers a probe), ``cut``
        (its first run closes in the middle of its first answer), ``stall``
        (its first run's probes hit the script watchdog), ``stall-always``
        (every run's probes do), ``stall-start`` (its first run stalls before
        its reference), ``die`` (its first run ends after the reference)."""
        self.replay_hang = replay_hang
        """A replay that runs until it is killed."""
        self.replay_error = replay_error
        """A line the replay's script prints as an error (``! …``) and runs
        on past, as Mesen does when a callback fails."""
        self.launched: list[str] = []
        self.procs: list[FakeProc] = []
        self.runs = 0
        """Probe servers started."""

    def consoles(self):
        return ("test",)

    def available(self):
        return True

    def native_path(self, path: str) -> str:
        return os.path.abspath(path)

    def launch(self, rom, role, script, log=None):
        self.launched.append(role)
        with open(script, encoding="utf-8") as fh:
            text = _cfg(fh.read())
        if role == "replay":
            proc = FakeProc(lambda proc: self._replay(text, log, proc))
        elif role == "probe":
            self.runs += 1
            run = self.runs
            proc = FakeProc(lambda proc: self._probe(text, proc, run))
        else:
            proc = FakeProc(lambda proc: proc.stopped.wait())
        self.procs.append(proc)
        return proc

    def _replay(self, script: str, log: str, proc: FakeProc) -> None:
        if self.replay_hang:
            with open(log, "w") as fh:
                fh.write("replaying\n")
            proc.stopped.wait()
            return
        path = _str(script, "evidence")
        if path:
            with open(path, "w") as fh:
                fh.write(self.game.log())
            with open(path + ".snesWorkRam", "wb") as fh:
                fh.write(self.game.ram())
        h = _str(script, "hash") if self.match else "BEEF"
        with open(log, "w") as fh:
            if self.replay_error:
                fh.write(f"! {self.replay_error}\n")
            fh.write(
                f"replay frame={CAPTURE_FRAME} poll=10 hash={h} "
                f"match={1 if self.match else 0} events=1\n"
            )

    def _probe(self, script: str, proc: FakeProc, run: int = 1) -> None:
        port = _num(script, "port")
        obs = _list(script, "obs")
        robs = _list(script, "robs")
        vobs = _list(script, "vobs")
        obs_from = _num(script, "obsFrom") or 0
        obs_to = _num(script, "obsTo")
        obs_to = 10**12 if obs_to is None else obs_to
        folder = _str(script, "dir")
        sock = socket.create_connection(("127.0.0.1", port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        game = self.game
        st = {"vobs": vobs[1] if vobs else None, "win": None, "rfrom": None}

        def send(line: str) -> None:
            data = (line + "\n").encode()
            if self.split and len(data) > 2:
                sock.sendall(data[: len(data) // 2])
                sock.sendall(data[len(data) // 2 :])
            else:
                sock.sendall(data)

        def observed(events):
            if not obs:
                return []
            lo, hi = obs[1], obs[2]
            return [
                (f, a, v)
                for kind, _, a, v, f in events
                if kind == "W" and lo <= (a & 0x1FFFF) <= hi and obs_from <= f <= obs_to
            ]

        def settle(events, lo, hi, until):
            last = 0
            before = {}
            for kind, _, a, v, f in events:
                if kind == "V" and lo <= a <= hi and f <= until and before.get(a) != v:
                    before[a] = v
                    last = f
            return last + 1

        base = game.run()
        ref = observed(base)
        if self.fault == "stall-start" and run == 1:
            send("stall Maximum execution time (1 seconds) exceeded.")
            proc.stopped.wait()
            sock.close()
            return
        send(f"ref {len(ref)} {max((w[0] for w in ref), default=0)} 0,10")
        send("refw " + " ".join(f"{f}:{a:X}:{v:02X}" for f, a, v in ref))
        if self.fault == "die" and run == 1:
            sock.close()
            return
        buf = b""
        while not proc.stopped.is_set():
            sock.settimeout(0.2)
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                words = line.decode().split(" ")
                if words[0] == "quit":
                    sock.close()
                    return
                if words[0] == "shot":
                    with open(os.path.join(folder, words[3]), "wb") as fh:
                        fh.write(b"\x89PNG" + line)
                    send(f"shot {words[2]}")
                    continue
                if words[0] == "settle":
                    lo, hi = int(words[2]), int(words[3])
                    send(f"settled {settle(base, lo, hi, st['vobs'])}")
                    continue
                if words[0] == "vobs":
                    st["vobs"] = int(words[2])
                    send(f"vobs {words[2]}")
                    continue
                if words[0] == "vwin":
                    st["win"] = [int(x) for x in words[1:]]
                    send(f"vwin {words[1]} {words[2]}")
                    continue
                if words[0] == "rfrom":
                    st["rfrom"] = int(words[1], 16)
                    send(f"rfrom {words[1]}")
                    continue
                if self.fault == "silent":
                    continue
                if (self.fault == "stall" and run == 1) or self.fault == "stall-always":
                    send("stall Maximum execution time (1 seconds) exceeded.")
                    continue
                if self.fault == "cut" and run == 1:
                    sock.sendall(f"res {words[1]} sa".encode())
                    sock.close()
                    return
                pid, si, mode = words[1], int(words[2]), words[3]
                rom = bytearray(game.rom)
                writes, subs = _changes(words[4] if len(words) > 4 else "")
                for a, v in writes:
                    rom[a] = v
                start = (0, 10)[si - 1]
                full = game.run(bytes(rom), subs) if subs else game.run(bytes(rom))
                events = [e for e in full if e[4] >= start]
                got = observed(events)
                k0 = next((i for i, w in enumerate(ref) if w[0] >= start), len(ref))
                want = ref[k0:]
                reads = ""
                if robs:
                    lo, hi, pc, frm = robs[0], robs[1], robs[2], robs[3]
                    rs = [
                        a
                        for kind, epc, a, _, f in events
                        if kind == "E"
                        and lo <= a <= hi
                        and f >= frm
                        and (pc is None or epc == pc)
                    ]
                    if st["rfrom"] is not None and st["rfrom"] in rs:
                        rs = rs[rs.index(st["rfrom"]) :]
                    elif st["rfrom"] is not None:
                        rs = []
                    rs = rs[:64]
                    if len(robs) > 5 and robs[5]:
                        rs = rs[: robs[5]]
                    reads = " r=" + ",".join(f"{a:X}" for a in rs)
                if vobs:
                    win = st["win"]
                    lo, hi = (win[0], win[1]) if win else (0, 1 << 20)
                    vref = game.vram(base, st["vobs"])
                    if win:
                        mine = game.vram(events)
                        at = settle(events, lo, hi, 1 << 30)
                    else:
                        mine = game.vram(events, st["vobs"])
                    keys = sorted(
                        a
                        for a in set(vref) | set(mine)
                        if lo <= a <= hi and vref.get(a) != mine.get(a)
                    )
                    d = ",".join(f"{a:X}:{mine.get(a, 0):X}" for a in keys)
                    reads += (
                        f" v={len(keys)},{keys[0] if keys else -1},"
                        f"{keys[-1] if keys else -1} d={d}"
                    )
                    if win:
                        reads += f" s={at}"
                if [(a, v) for _, a, v in got] == [(a, v) for _, a, v in want]:
                    send(f"res {pid} same{reads}")
                    continue
                pos = next(
                    (
                        i + 1
                        for i, (g, w) in enumerate(zip(got, want, strict=False))
                        if g[1:] != w[1:]
                    ),
                    min(len(got), len(want)) + 1,
                )
                vals = got[:pos] if mode == "first" else got
                send(
                    f"res {pid} diff {pos} {len(want)} "
                    + ".".join(f"{v:02X}" for _, _, v in vals)
                    + reads
                )
        sock.close()


class StubProbe:
    """A probe server for the tracer's pointer stage alone: each probe
    answers where the string's first read lands, by ``where`` over the ROM as
    changed."""

    n = no_answer = 0

    def __init__(self, rom: bytes, where):
        self.rom, self.where, self.answer = rom, where, None

    def start(self):
        yield from ()

    def effect(self, writes, frame, subs=None):
        from types import SimpleNamespace

        rom = bytearray(self.rom)
        for a, v in writes:
            rom[a] = v & 0xFF
        at = self.where(bytes(rom))
        self.answer = SimpleNamespace(offsets=[] if at is None else [at])
        yield from ()


def pointer_stage(folder: str, console: Console, rom, evs, first: int, where):
    """The tracer's pointer stage over synthetic evidence and a stub probe
    server: its Result."""
    from mapchar.capture import evidence
    from mapchar.capture.protocol import drive
    from mapchar.capture.trace import Result, Tracer

    t = object.__new__(Tracer)
    t.ev = evidence.Evidence.of(folder, CONSOLE, evs)
    t.result = Result()
    t.rom, t.console, t._servers = bytes(rom), console, []
    t._server = lambda **kw: StubProbe(bytes(rom), where)
    drive(t._pointers(first, frozenset(), None))
    return t.result
