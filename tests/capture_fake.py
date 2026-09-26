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


class Game:
    """Shows message ``message`` at frame 3; the capture point is frame 10."""

    def __init__(self, rom: bytes, message: int = 1):
        self.rom = rom
        self.message = message

    def run(self, rom: bytes | None = None) -> list[tuple]:
        """Every event as ``(kind, pc, address, value, frame)``."""
        rom = self.rom if rom is None else rom
        ev = []
        for a in range(0x800, 0x810):  # something else the game reads each frame
            ev.append(("E", NOISE_PC, a, rom[a], 1))
        slot = TABLE + 2 * self.message
        ev.append(("E", POINTER_PC, slot, rom[slot], 3))
        ev.append(("E", POINTER_PC, slot + 1, rom[slot + 1], 3))
        at = int.from_bytes(rom[slot : slot + 2], "little")
        for k in range(64):
            if at + k >= len(rom):
                break
            v = rom[at + k]
            ev.append(("E", READER_PC, at + k, v, 3))
            if v == END:
                break
            ev.append(("W", WRITER_PC, BUFFER + k, v, 3))
        for a in range(0x800, 0x810):
            ev.append(("E", NOISE_PC, a, rom[a], 6))
        return ev

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


def write_moment(folder: str) -> None:
    """A moment as the recorder leaves it: one state at frame 0."""
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "moment.txt"), "w") as fh:
        fh.write(
            f"capture frame={CAPTURE_FRAME} poll=10 hash=CAFE\nstate 1 frame=0 poll=0\n"
        )
    with open(os.path.join(folder, "s01.mss"), "wb") as fh:
        fh.write(b"state")
    with open(os.path.join(folder, "input.txt"), "w") as fh:
        fh.write("".join(f"{p} \n" for p in range(1, 11)))
    with open(os.path.join(folder, "screen.png"), "wb") as fh:
        fh.write(b"\x89PNG")


def _str(script: str, key: str) -> str | None:
    m = re.search(rf"\b{key} = \[==\[(.*?)\]==\]", script)
    return m.group(1) if m else None


def _num(script: str, key: str) -> int | None:
    m = re.search(rf"\b{key} = (\d+)", script)
    return int(m.group(1)) if m else None


def _list(script: str, key: str) -> list | None:
    m = re.search(rf"\b{key} = \{{ (.*?) \}}", script)
    if not m:
        return None
    out = []
    for part in m.group(1).split(", "):
        if part.startswith("[==["):
            out.append(part[4:-4])
        elif part == "nil":
            out.append(None)
        else:
            out.append(int(part))
    return out


class FakeProc:
    """What :func:`subprocess.Popen` returns, for a thread."""

    def __init__(self, target):
        self.returncode = None
        self.stopped = threading.Event()

        def run():
            try:
                target(self)
            finally:
                self.returncode = 0

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def poll(self):
        return self.returncode

    def kill(self):
        self.stopped.set()
        self.thread.join(timeout=5)
        self.returncode = -9

    def wait(self, timeout=None):
        self.thread.join(timeout)
        return self.returncode


class FakeEmulator:
    id = "fake"
    name = "Fake"

    def __init__(self, game: Game, match: bool = True, split: bool = True):
        self.game = game
        self.match = match
        self.split = split
        """Send every line in two pieces, as a socket may deliver them."""
        self.launched: list[str] = []

    def consoles(self):
        return ("test",)

    def available(self):
        return True

    def native_path(self, path: str) -> str:
        return os.path.abspath(path)

    def launch(self, rom, role, script, log=None):
        self.launched.append(role)
        with open(script, encoding="utf-8") as fh:
            text = next(line for line in fh if line.startswith("CFG = "))
        if role == "replay":
            return FakeProc(lambda proc: self._replay(text, log))
        if role == "probe":
            return FakeProc(lambda proc: self._probe(text, proc))
        return FakeProc(lambda proc: proc.stopped.wait())

    def _replay(self, script: str, log: str) -> None:
        path = _str(script, "evidence")
        if path:
            with open(path, "w") as fh:
                fh.write(self.game.log())
            with open(path + ".snesWorkRam", "wb") as fh:
                fh.write(self.game.ram())
        h = _str(script, "hash") if self.match else "BEEF"
        with open(log, "w") as fh:
            fh.write(
                f"replay frame={CAPTURE_FRAME} poll=10 hash={h} "
                f"match={1 if self.match else 0} events=1\n"
            )

    def _probe(self, script: str, proc: FakeProc) -> None:
        port = _num(script, "port")
        obs = _list(script, "obs")
        robs = _list(script, "robs")
        obs_from = _num(script, "obsFrom") or 0
        obs_to = _num(script, "obsTo")
        obs_to = 10**12 if obs_to is None else obs_to
        folder = _str(script, "dir")
        sock = socket.create_connection(("127.0.0.1", port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

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

        ref = observed(self.game.run())
        send(f"ref {len(ref)} {max((w[0] for w in ref), default=0)} 0,10")
        send("refw " + " ".join(f"{f}:{a:X}:{v:02X}" for f, a, v in ref))
        buf = b""
        while not proc.stopped.is_set():
            sock.settimeout(0.2)
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
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
                pid, si, mode = words[1], int(words[2]), words[3]
                rom = bytearray(self.game.rom)
                if len(words) > 4 and words[4]:
                    for w in words[4].split(","):
                        a, v = w.split(":")
                        rom[int(a, 16)] = int(v, 16)
                start = (0, 10)[si - 1]
                events = [e for e in self.game.run(bytes(rom)) if e[4] >= start]
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
                    ][:64]
                    if len(robs) > 5 and robs[5]:
                        rs = rs[: robs[5]]
                    reads = " r=" + ",".join(f"{a:X}" for a in rs)
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
