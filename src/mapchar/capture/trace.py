"""Tracing a capture: every answer decided by changing ROM bytes and watching
what the text becomes.

- **Sources.** For each output, candidates in order — the byte after the last
  source, the reads of the value written, the rest most recent first — in a
  window of reads (this frame and the one before), widened (to 4 frames, 16,
  all) only while nothing in it is a source. The byte's lowest bit is flipped,
  and within the window the strongest relation wins: *copied* (the output
  moves by what the byte moved), *only this* (only substitutions, this output
  among them), *determines* (this output is the first to change). For VRAM
  output, a read's byte is confirmed when its change reaches VRAM.
- **Pointers.** A byte holds the string's address when changing it by 2 moves
  the string's first read by 2 × 1, 2 or 4 (the code unit), or 256 times that
  for the byte above. Candidates are the reads before the first read, and every
  place in the ROM holding its address that this replay read or executed — on
  the Game Boy only in bank 0 or the string's own bank; there and on the
  Master System an ``ld rr,nn`` operand is named so. On the GBA every other
  word holding a confirmed pointer's value is listed and classed; on the Game
  Boy a string that follows a ``call`` read by the called routine has the call
  site for its reference.
- **Codes.** Every byte of a source's code unit is set to each value: with RAM
  output, what replaces the output is a character, a string, nothing, or a
  structural change; with VRAM output, the reader's next reads say printable,
  a command skipping *n* bytes, or the end, and the VRAM left groups the
  printable codes by glyph.
- **Streams.** When nearly every output is copied from a dictionary, the stream
  is the run of consecutive bytes, read during the text, whose first changed
  output moves forward with the address; :mod:`~mapchar.capture.bitlayout`
  finds its layout.

What the replay recorded only orders what is tried; a budget only bounds how
long a search goes on, and past it a thing is reported not found. A byte the
game reads more than once has only the read in question changed (the probe
substitutes what that read returns), and text the reader takes from a RAM
buffer has its codes swept there.
"""

from __future__ import annotations

import bisect
import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from operator import le

from mapchar.capture import bitlayout
from mapchar.capture.consoles import Console
from mapchar.capture.emulator import Emulator
from mapchar.capture.evidence import Evidence, Moment
from mapchar.capture.occurrence import Occurrence
from mapchar.capture.probe import (
    STABLE_DEADLINE,
    STABLE_FRAMES,
    ProbeServer,
    ReadSub,
    reader_cpu,
)
from mapchar.capture.protocol import CaptureError, Step

WINDOWS = (1, 4, 16, None)
"""Frames of reads searched for a source, widening while none is found."""

OTHER_BUDGET = 16
"""Candidates tried per window among reads of other values."""

POINTER_READS = 64
"""Reads before the string tried as pointers."""

POINTER_BUDGET = 200
"""Pointer candidates tried in all."""

PACKED = 0.3
"""Below this share of outputs whose sources advance through one stream, the
text is packed: its outputs come from a dictionary, not the stream."""

STEP = 8
"""How far the stream may advance between two outputs' sources."""

MOVES = (1, 2, 4, 256, 512, 1024)
"""How far a pointer change of 1 may move the string's first read."""

EXTENT_FRAMES = 4
"""How many frames the string's reader may take between two of its reads
and still be reading the string."""

RAM_CANDIDATES = 16
"""Earlier RAM writes of an output's value tried as the buffer it is read
from, when no ROM byte is its source."""

COPIES_MAX = 64
"""Other words holding a confirmed GBA pointer's value listed at most."""

TEXT_RUN = 8
"""Printable bytes around a would-be ``ldr`` that make it text instead."""

COPY_NEAR = 4
"""Words either side of a copy looked at for another pointer: with none, the
copy sits among graphics or data and most likely holds the bytes by chance."""

GB_OPERANDS = {0x21: "ld hl,nn", 0x11: "ld de,nn", 0x01: "ld bc,nn"}
"""Game Boy (and Z80: Master System) instructions whose 16-bit operand is
commonly a string's address."""

OPERAND_CONSOLES = ("gb", "sms")
"""The profiles whose pointers in code are named by :data:`GB_OPERANDS`."""

GB_CALLS = (0xCD, 0xC4, 0xCC, 0xD4, 0xDC)
"""Game Boy ``call nn`` and its conditional forms."""

CALL_GAP = 2
"""Bytes at most between a call's end and a string that follows it."""

CALL_REACH = 0x100
"""How far past a call's target its reader PC may lie and still be the called
routine."""


# -- results


@dataclass
class Source:
    output: int
    """Position among the occurrence's outputs."""
    byte: int
    """ROM offset."""
    tier: str
    """``copied``, ``only-this`` or ``determines``; ``vram`` for bitmap text."""
    changed: list[int] = field(default_factory=list)
    """Reference positions its change alters."""
    stream: bool = True
    """Among the sources that advance through the string in order; False for
    one read elsewhere — a dictionary's."""


@dataclass
class Pointer:
    address: int
    """ROM offset of the byte."""
    moves: int
    """How far changing it moved the string's first read."""
    why: str
    """``read before`` or ``holds the address``."""
    note: str = ""
    """What the byte is, when that says more: the instruction a pointer in
    code is the operand of (``ld hl,nn``)."""


@dataclass
class Copy:
    """Another word holding a confirmed pointer's value: compiled code keeps a
    pointer in a literal pool and in initialised data, sometimes twice, and a
    repoint must rewrite each copy the game uses. Never itself confirmed."""

    address: int
    """ROM offset of the 4-aligned word."""
    of: int
    """ROM offset of the confirmed pointer it copies."""
    kind: str
    """``touched`` (this replay read it), ``literal`` (an ``ldr`` reaches it),
    ``other``, or ``data`` (other, with no pointer in the words around it:
    most likely graphics or data holding the same bytes by chance)."""


@dataclass
class Code:
    value: int
    kind: str
    """RAM output: ``char``, ``string``, ``empty``, ``same``, ``line`` (the
    character in :attr:`out`, then the line ends) or the shape of a structural
    change (``truncate``, ``extend``, ``substitute``, ``shift``,
    ``rewrite``).
    VRAM output: ``printable``, ``command`` or ``end``.
    Either: ``stall`` when the probe got no answer."""
    out: list[int] = field(default_factory=list)
    """RAM output: the codes written in the output's place."""
    skip: int = 0
    """A command's parameter bytes."""
    image: str = ""
    """VRAM output: a digest of what the VRAM holds, grouping by glyph."""


@dataclass
class Result:
    output: str = ""
    """``ram`` or ``vram``: what the text was observed as."""
    how: str = ""
    reader: int | None = None
    region: tuple[str, int, int] | None = None
    frames: tuple[int, int] = (0, 0)
    typed: list[tuple[str, int, int | None]] = field(default_factory=list)
    """``(character, output code, source byte)`` per typed letter."""
    decoder: dict[int, str] = field(default_factory=dict)
    gaps: list[tuple[str, list[int]]] = field(default_factory=list)
    outputs: int = 0
    sources: list[Source] = field(default_factory=list)
    unit: int = 1
    string: tuple[int, int] | None = None
    """The string's first byte and its last, the end token included: where
    its reader stopped reading in order, ROM offsets."""
    first_read: int | None = None
    """The string's first read, as the pointer test measured it."""
    pointers: list[Pointer] = field(default_factory=list)
    stalls: list[int] = field(default_factory=list)
    """Pointer candidates whose change stalled the game: no answer in time."""
    strays: list[int] = field(default_factory=list)
    """Pointer candidates after whose change the reader read nothing of the
    string's neighbourhood: it moved out of reach, or was not shown."""
    code_byte: int | None = None
    code_ram: tuple[str, int] | None = None
    """``(memory type, offset)`` of the RAM byte the codes were swept at, for
    text its reader takes from a RAM buffer."""
    codes: list[Code] = field(default_factory=list)
    packed: bool = False
    stream: tuple[int, int] | None = None
    layouts: list[dict] = field(default_factory=list)
    tokens: list = field(default_factory=list)
    """Packed text: each output's dictionary address or ``["value", v]``."""
    settled: int | None = None
    probes: int = 0
    no_answer: int = 0
    notes: list[str] = field(default_factory=list)
    copies: list[Copy] = field(default_factory=list)
    """Other words holding a confirmed GBA pointer's value."""
    follows_call: int | None = None
    """ROM offset of the ``call`` the string follows, on the Game Boy: the
    called routine reads the string from its return address, so the call
    site is the string's reference and no pointer holds it."""

    def to_json(self) -> dict:
        d = asdict(self)
        d["decoder"] = {str(k): v for k, v in self.decoder.items()}
        return d

    @classmethod
    def from_json(cls, d: dict) -> Result:
        r = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        r.decoder = {int(k): v for k, v in d.get("decoder", {}).items()}
        r.sources = [Source(**s) for s in d.get("sources", [])]
        r.pointers = [Pointer(**p) for p in d.get("pointers", [])]
        r.copies = [Copy(**c) for c in d.get("copies", [])]
        r.codes = [Code(**c) for c in d.get("codes", [])]
        for key in ("region", "frames", "string", "stream", "code_ram"):
            if r.__dict__.get(key) is not None:
                setattr(r, key, tuple(getattr(r, key)))
        r.typed = [tuple(t) for t in r.typed]
        r.gaps = [(s, list(g)) for s, g in r.gaps]
        return r


def layout_json(lay: bitlayout.Layout) -> dict:
    return {
        "order": lay.order,
        "width": lay.width,
        "escapes": lay.escapes,
        "start": lay.start,
        "table": [
            [c, m if m == bitlayout.SILENT else _meaning_json(m)]
            for c, m in sorted(lay.table.items())
        ],
    }


def _meaning_json(m):
    a0, n = m
    return [list(a0) if isinstance(a0, tuple) else a0, n]


def layout_from_json(d: dict) -> bitlayout.Layout:
    table = {}
    for c, m in d["table"]:
        if m == bitlayout.SILENT:
            table[c] = m
        else:
            a0, n = m
            table[c] = (tuple(a0) if isinstance(a0, list) else a0, n)
    return bitlayout.Layout(d["order"], d["width"], d["escapes"], d["start"], table)


# -- what the evidence orders


class Reads:
    """The ROM reads before an output, each byte once at its most recent
    read, most recent first; kept as the log is walked forward to each
    output."""

    def __init__(self, evs, frames: list[int] | None = None):
        self.evs = evs
        self.frames = [e[3] for e in evs] if frames is None else frames
        self.sorted = all(map(le, self.frames, self.frames[1:]))
        self.at = 0
        self.last: dict[int, int] = {}
        self.byval: dict[int, dict[int, int]] = defaultdict(dict)

    def window(self, i, back, tried, value):
        """``(same, other)``: the bytes read before ``evs[i]`` down to its
        frame less ``back`` (None: all), not in ``tried``, whose latest read
        gave ``value`` / did not (at most :data:`OTHER_BUDGET` of those), as
        ``(byte, frame of that read, index of that read)``."""
        evs = self.evs
        if not self.sorted:
            return self._walk(i, back, tried, value)
        if i < self.at:
            self.at, self.last, self.byval = 0, {}, defaultdict(dict)
        last, byval = self.last, self.byval
        for j in range(self.at, i):
            e = evs[j]
            if e[0] == "E":
                b = e[1]
                pj = last.pop(b, None)
                if pj is not None:
                    del byval[evs[pj][2]][b]
                last[b] = j
                byval[e[2]][b] = j
        self.at = i
        lo = (
            0
            if back is None
            else bisect.bisect_left(self.frames, evs[i][3] - back, 0, i)
        )
        same, other = [], []
        for b, j in reversed(byval[value].items()):
            if j < lo:
                break
            if b not in tried:
                same.append((b, evs[j][3], j))
        for b, j in reversed(last.items()):
            if j < lo:
                break
            if b in tried:
                continue
            e = evs[j]
            if e[2] != value:
                other.append((b, e[3], j))
                if len(other) == OTHER_BUDGET:
                    break
        return same, other

    def _walk(self, i, back, tried, value):
        """:meth:`window` by walking back from ``evs[i]``: for a log whose
        frames are out of order, and the tests' oracle."""
        evs = self.evs
        seen, same, other = set(), [], []
        for j in range(i - 1, -1, -1):
            e = evs[j]
            if back is not None and e[3] < evs[i][3] - back:
                break
            if e[0] == "E" and e[1] not in seen:
                seen.add(e[1])
                if e[1] not in tried:
                    (same if e[2] == value else other).append((e[1], e[3], j))
        return same, other[:OTHER_BUDGET]


def vram_unit(occ_unit: int, values: list[int]) -> int:
    """The width of a code read straight into VRAM: the occurrence's, or 2
    when a read gave more than a byte (a halfword read of a 1-byte-coded
    occurrence)."""
    return max(occ_unit, 2 if any(v > 0xFF for v in values) else 1)


def advancing_run(sources: list[int]) -> list[int]:
    """The longest run of sources, in output order, each just past the one
    before (within :data:`STEP`): the positions in ``sources`` of the
    string's own bytes. A dictionary's bytes, read before, after or between
    them, are left out."""
    n = len(sources)
    if not n:
        return []
    best, prev = [1] * n, [-1] * n
    for i in range(n):
        b = sources[i]
        for j in range(i):
            if sources[j] < b <= sources[j] + STEP and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    i = max(range(n), key=lambda x: (best[x], -x))
    run = []
    while i >= 0:
        run.append(i)
        i = prev[i]
    return run[::-1]


def advancing(sources: list[int]) -> float:
    """The share of outputs whose source lies just past the one before along
    the longest such run: near 1 for text read from one stream, whatever it
    expands on the way; near 0 for text copied from a dictionary."""
    if len(sources) < 2:
        return 1.0
    return (len(advancing_run(sources)) - 1) / (len(sources) - 1)


def edits_kind(ref: list[int], got: list[int]) -> str:
    """The shape of a change to the output: ``none``, ``extend`` (the output
    ran on), ``truncate`` (it stopped early), ``substitute``, ``shift`` or
    ``rewrite``."""
    if got == ref:
        return "none"
    if len(got) > len(ref) and got[: len(ref)] == ref:
        return "extend"
    if len(got) < len(ref) and ref[: len(got)] == got:
        return "truncate"
    ops = [
        op
        for op in SequenceMatcher(None, ref, got, autojunk=False).get_opcodes()
        if op[0] != "equal"
    ]
    if (
        len(ops) == 1
        and ops[0][0] == "replace"
        and ops[0][2] - ops[0][1] == ops[0][4] - ops[0][3]
    ):
        return "substitute"
    if all(op[0] in ("insert", "delete") for op in ops):
        return "shift"
    return "rewrite"


def replaced(ref: list[int], got: list[int], k: int) -> list[int] | None:
    """The codes that replace output ``k`` when ``got`` is ``ref`` with that
    one output replaced and the rest unchanged as far as the range shows; None
    when the change did more — ended the text early, among other things."""
    if got[:k] != ref[:k]:
        return None
    rest = ref[k + 1 :]
    if len(got) == len(ref) and got[k + 1 :] == rest:
        return got[k : k + 1]  # one for one, whatever the rest repeats
    need = min(3, len(rest))
    for n in range(0, len(got) - k + 1):
        tail = got[k + n :]
        m = min(len(tail), len(rest))
        if m >= need and m and tail[:m] == rest[:m]:
            return got[k : k + n]
        if not rest and n == len(got) - k:
            return got[k:]
    return None


LINE_REST = 8
"""How much of the text after a line break must follow unchanged."""


def line_break(ref: list[int], got: list[int], k: int) -> int | None:
    """What output ``k`` became when the change made it end its line: it is
    written (as itself or another value), padding is inserted just after it
    — past the output's own attribute, at most — and the text after it
    follows unchanged for a while, the box's end aside."""
    if k >= len(got) or got[:k] != ref[:k]:
        return None
    ops = [
        op
        for op in SequenceMatcher(None, ref, got, autojunk=False).get_opcodes()
        if op[0] != "equal"
    ]
    i = 0
    if ops and ops[0][0] == "replace" and ops[0][1:3] == (k, k + 1):
        if ops[0][4] - ops[0][3] != 1:
            return None
        i = 1
    if i >= len(ops) or ops[i][0] != "insert" or not k < ops[i][1] <= k + 2:
        return None
    if ops[i][4] - ops[i][3] < 2:
        return None
    if i + 1 < len(ops) and ops[i + 1][1] < ops[i][1] + LINE_REST:
        return None
    return got[k]


# -- what the pointer stage reads off the ROM itself


def _gba_pointer(v: int) -> bool:
    return 0x08000000 <= v < 0x0A000000


def _in_text(rom: bytes, i: int) -> bool:
    """Whether the bytes around ``i`` are a run of printable ASCII: text,
    whose ``H``-``O`` look like a Thumb ``ldr``'s opcode byte."""
    run = rom[max(0, i - TEXT_RUN // 2) : i + TEXT_RUN // 2 + 2]
    return len(run) >= TEXT_RUN and all(0x20 <= b < 0x7F for b in run)


def ldr_reaches(rom: bytes, k: int, executed=None) -> bool:
    """Whether a PC-relative load reaches the 4-aligned word at ``k``: a Thumb
    ``ldr rX, [pc, #imm]`` (``$48-$4F``, up to 1020 bytes on) or an ARM
    ``ldr rX, [pc, #±imm]`` (4095 bytes either way). The instruction must be
    code this replay ran, by ``executed(i)``; not knowing that, it must at
    least not sit in a run of text. Reached is not read: the bytes may still
    be data that decodes as one."""

    def code(i: int) -> bool:
        return executed(i) if executed is not None else not _in_text(rom, i)

    for imm in range(256):
        for i in (k - 4 * imm - 4, k - 4 * imm - 2):
            if i >= 0 and rom[i] == imm and rom[i + 1] >> 3 == 0x09 and code(i):
                return True
    for imm in range(0, 4096, 4):
        for i, up in ((k - 8 - imm, 0x00800000), (k - 8 + imm, 0)):
            if 0 <= i <= len(rom) - 4:
                w = int.from_bytes(rom[i : i + 4], "little")
                if (
                    w >> 28 != 0xF
                    and w & 0x0FFF0000 == 0x051F0000 | up
                    and w & 0xFFF == imm
                    and code(i)
                ):
                    return True
    return False


def gba_copies(rom: bytes, at: int, touched) -> Step[list[Copy]]:
    """The other 4-aligned words holding the 32-bit value at ``at``, each
    classed: read by this replay (``touched(k)``; None when not known), in
    reach of an ``ldr``, or neither — and among neither, those with no
    pointer within :data:`COPY_NEAR` words marked ``data``, listed last."""
    value = rom[at : at + 4]
    found = []
    k = rom.find(value)
    while k >= 0 and len(found) < COPIES_MAX:
        if k != at and k % 4 == 0:
            found.append(k)
        k = rom.find(value, k + 1)
    yield
    out = []
    for n, k in enumerate(found):
        if touched is not None and touched(k):
            kind = "touched"
        elif ldr_reaches(rom, k, touched):
            kind = "literal"
        else:
            near = [
                int.from_bytes(rom[x : x + 4], "little")
                for x in range(k - 4 * COPY_NEAR, k + 4 * COPY_NEAR + 4, 4)
                if x != k and 0 <= x <= len(rom) - 4
            ]
            kind = "other" if any(map(_gba_pointer, near)) else "data"
        out.append(Copy(k, at, kind))
        if n % 8 == 7:
            yield
    order = ("touched", "literal", "other", "data")
    return sorted(out, key=lambda c: (order.index(c.kind), c.address))


def gb_operand(rom: bytes, k: int, z80: bool = False) -> str:
    """The Game Boy or Z80 instruction the 16-bit operand at ``k`` belongs
    to, when it is one that loads an address into a register pair; else
    empty. On the Z80 a ``$DD``/``$FD`` prefix makes ``$21`` load ``ix`` or
    ``iy``."""
    if k < 1:
        return ""
    if z80 and k >= 2 and rom[k - 1] == 0x21 and rom[k - 2] in (0xDD, 0xFD):
        return "ld ix,nn" if rom[k - 2] == 0xDD else "ld iy,nn"
    return GB_OPERANDS.get(rom[k - 1], "")


def gb_bank_fits(rom: bytes, k: int, target: int) -> bool:
    """Whether the 16-bit address at ``k`` can reach ``target``: read from
    bank 0, or from the switched bank the target is in, or followed by a byte
    naming the target's bank (a far pointer: address, then bank); a target in
    bank 0 is reached from anywhere."""
    bank = target // 0x4000
    if not bank or k < 0x4000 or k // 0x4000 == bank:
        return True
    return k + 2 < len(rom) and rom[k + 2] == bank


def gb_follows_call(rom: bytes, first: int, reader: int | None) -> int | None:
    """The offset of a ``call`` the string at ``first`` follows, when the
    reading PC is that call's routine: it pops its return address and reads
    the string from there. ``reader`` is the PC as the Game Boy profile logs
    it, a ROM offset (bit 24 set for code running from RAM, which no call
    into ROM names)."""
    if reader is None or reader >> 24:
        return None
    pc = reader if reader < 0x4000 else 0x4000 | (reader & 0x3FFF)
    for gap in range(CALL_GAP + 1):
        at = first - 3 - gap
        if at < 0 or rom[at] not in GB_CALLS:
            continue
        target = rom[at + 1] | rom[at + 2] << 8
        if 0 <= pc - target < CALL_REACH:
            return at
    return None


# -- the tracer


class Tracer:
    """Traces one capture. :meth:`run` is a step; :attr:`status` says what it
    is on, and :attr:`progress` how far through it, when that is counted."""

    def __init__(
        self,
        emulator: Emulator,
        rom_path: str,
        rom: bytes,
        console: Console,
        folder: str,
        ev: Evidence,
        occ: Occurrence,
    ):
        self.emulator, self.rom_path, self.rom = emulator, rom_path, rom
        self.console, self.folder, self.ev, self.occ = console, folder, ev, occ
        self.moment = Moment.load(folder)
        self.result = Result()
        self.status = ""
        self.progress: tuple[int, int] | None = None
        """Done and total of the stage :attr:`status` names, or None."""
        self._servers: list[ProbeServer] = []

    def _server(self, **kw) -> ProbeServer:
        p = ProbeServer(self.emulator, self.rom_path, self.console, self.folder, **kw)
        self._servers.append(p)
        return p

    def _at(self, status: str, done: int = 0, total: int = 0) -> None:
        self.status = status
        self.progress = (done, total) if total else None

    def close(self, wait: float = 5.0) -> None:
        """End every probe server; with ``wait`` 0 or less, kill them at
        once (see :meth:`ProbeServer.close`)."""
        for p in self._servers:
            p.close(wait)
        self._servers.clear()

    def _count(self) -> None:
        self.result.probes = sum(p.n for p in self._servers)
        self.result.no_answer = sum(p.no_answer for p in self._servers)

    def run(self) -> Step[Result]:
        done = False
        try:
            self._at("indexing the evidence")
            yield from self.ev.index()
            if self.occ.kind == "ram":
                yield from self._ram()
            else:
                yield from self._vram()
            done = True
        finally:
            self._count()
            # Stopped or failed: nothing is waited for.
            self.close(5.0 if done else 0)
        return self.result

    # -- what one byte's change is

    def _only_read(self, p: ProbeServer, b: int, j: int) -> tuple[int, int] | None:
        """``(pc, n)`` when ROM byte ``b`` is read more than once from the
        savestate a probe of read ``j`` starts at: the read to change is the
        n-th by that PC. None when changing the byte changes that read
        alone."""
        evs = self.ev.events
        si = p.state_for(evs[j][3]) or 1
        start = p.states[si - 1] if p.states else 0
        # On the GBA a halfword or word read is logged at its aligned
        # address: every read from the byte's word start up to the byte
        # counts, as the probe script counts them.
        lo = b & ~3 if self.console.lua == "gba" else b
        reads = self.ev.reads_at
        after = sorted(
            i for a in range(lo, b + 1) for i in reads.get(a, ()) if evs[i][3] >= start
        )
        if len(after) <= 1:
            return None
        pc = evs[j][4]
        return pc, sum(1 for i in after if i <= j and evs[i][4] == pc)

    def _change(self, p: ProbeServer, b: int, j: int, v: int):
        """``(writes, reads)`` that give read ``j`` of ROM byte ``b`` the value
        ``v``: the byte written, or only that read's value substituted."""
        once = self._only_read(p, b, j)
        if once is None:
            return [(b, v)], ()
        pc, n = once
        return [], (ReadSub(self.console.rom, b, v, pc, n),)

    # -- RAM output

    def _ram(self) -> Step[None]:
        evs, occ, r = self.ev.events, self.occ, self.result
        occ_w = occ.events
        span = sorted({evs[i][1] for i in occ_w})
        kinds = {self.console.ram_of(a)[0] for a in span}
        if len(kinds) != 1:
            raise CaptureError("the text is written across two memories")
        mt = kinds.pop()
        offs = [self.console.ram_of(a)[1] for a in span]
        f0, f1 = evs[occ_w[0]][3], evs[occ_w[-1]][3]
        r.output, r.how, r.reader = "ram", occ.how, occ.pc
        r.region, r.frames = (mt, min(offs), max(offs)), (f0, f1)
        r.decoder, r.gaps = dict(occ.decoder), occ.gaps
        r.outputs = len(occ_w)
        r.unit = getattr(occ, "unit", 1)
        self._at("starting the probe server")
        pages = range(min(span) >> 8, (max(span) >> 8) + 1)
        p = self._server(
            name="sources",
            obs=(mt, min(offs), max(offs)),
            obs_from=f0,
            obs_to=f1,
            bulk=[b for b in self.ev.bulk if b[0] in pages] or None,
        )
        yield from p.start()
        widx, src = yield from self._sources(p, occ_w, span, f0)
        pos = {i: k for k, i in enumerate(widx)}
        order_pos = {i: n for n, i in enumerate(occ_w)}
        firsts = sorted(src)
        byte_of = [src[k][0] for k in firsts]
        run = advancing_run(byte_of)
        # Text the reader takes backwards through the ROM advances downwards;
        # text found backwards (Occurrence.reverse) needs nothing more here.
        back = advancing_run([-b for b in byte_of])
        backwards = len(back) > len(run)
        run = [firsts[x] for x in (back if backwards else run)]
        in_run = set(run)
        r.sources = [
            Source(order_pos[widx[k]], s[0], s[2], s[1], k in in_run)
            for k, s in sorted(src.items())
        ]
        typed_at = dict(occ.letters)
        for i in occ_w:
            if i in typed_at:
                k = pos[i]
                r.typed.append(
                    (typed_at[i], evs[i][2], src[k][0] if k in src else None)
                )
        if not src:
            r.notes.append("no output was traced to a ROM byte")
            yield from self._ram_stream(p, occ_w, widx, span)
            return
        r.packed = len(firsts) > 1 and (len(run) - 1) / (len(firsts) - 1) < PACKED
        k_first, k_last = run[0], run[-1]
        if backwards:
            r.string = (src[k_last][0], src[k_first][0])
        else:
            r.string = (
                src[k_first][0],
                self._extent(src[k_last][0], at=src[k_last][3]),
            )
        first_src, reader = src[k_first][0], None
        if r.packed:
            yield from self._stream(p, widx, src, occ_w, f0, f1)
            if r.stream:
                first_src = r.stream[0]
                reader = self._first_reader(first_src)
        own = frozenset(s[0] for s in src.values())
        yield from self._pointers(first_src, own, reader)
        if not r.packed:
            yield from self._ram_codes(p, src)

    def _sources(self, p: ProbeServer, occ_w, span, f0) -> Step[tuple[list, dict]]:
        evs = self.ev.events
        lo, hi = min(span), max(span)
        f1 = evs[occ_w[-1]][3]
        reads = Reads(evs, self.ev.frames)
        if reads.sorted:
            a = bisect.bisect_left(reads.frames, f0)
            b = bisect.bisect_right(reads.frames, f1)
            rng = range(a, b)
        else:
            rng = range(len(evs))
        widx = [
            i
            for i in rng
            if evs[i][0] == "W" and f0 <= evs[i][3] <= f1 and lo <= evs[i][1] <= hi
        ]
        if len(widx) != p.nref:
            raise CaptureError(
                "the probe server's replay wrote the text differently from the "
                f"evidence ({p.nref} writes against {len(widx)})"
            )
        pos = {i: k for k, i in enumerate(widx)}
        src: dict[int, tuple[int, list[int], str, int]] = {}
        prev = None
        for n, i in enumerate(occ_w):
            self._at(f"sources: output {n + 1} of {len(occ_w)}", n, len(occ_w))
            k = pos[i]
            best, tried = None, set()
            for back in WINDOWS:
                same, other = reads.window(i, back, tried, evs[i][2])
                cands = same + other
                if prev is not None:
                    cands.sort(
                        key=lambda c, prev=prev: (c[0] != prev + 1, c[0] != prev - 1)
                    )
                tried |= {c[0] for c in cands}
                best = yield from self._tier(p, cands, k, best)
                if best:
                    break
            if best:
                src[k] = best
                prev = best[0]
                if p.control is None:
                    p.control = ([(best[0], self.rom[best[0]] ^ 0x01)], evs[best[3]][3])
            else:
                self.result.notes.append(f"output {n} has no source")
            self._count()
        return widx, src

    def _tier(self, p: ProbeServer, cands, k, best) -> Step:
        rom = self.rom
        for b, fr, j in cands:
            nv = rom[b] ^ 0x01
            r = yield from p.effect([(b, nv)], fr)
            if r is None:
                continue
            refv, got = r
            off = len(p.ref_values) - len(refv)
            kk = k - off
            first = next(
                (x for x in range(min(len(got), len(refv))) if got[x] != refv[x]), None
            )
            if (
                first is not None
                and first <= kk < len(got)
                and kk < len(refv)
                and got[kk] - refv[kk] == nv - rom[b]
                and (len(got) == len(refv) or first == kk)
            ):
                changed = (
                    [x + off for x in range(len(got)) if got[x] != refv[x]]
                    if len(got) == len(refv)
                    else [k]
                )
                return (b, changed, "copied", j)
            if len(got) == len(refv):
                changed = [x + off for x in range(len(got)) if got[x] != refv[x]]
                if k in changed and (best is None or best[2] == "determines"):
                    best = (b, changed, "only-this", j)
            if best is None and first is not None and first + off == k:
                best = (b, [k], "determines", j)
        return best

    def _extent(
        self, last: int, reader: int | None = None, at: int | None = None
    ) -> int:
        """The string's last byte: the reader of its last source goes on
        reading past it, in order, to the end token or the string's end.
        Followed from read ``at`` (the one the source was confirmed with),
        else the byte's first read; the reader's reads may be spread over
        frames, :data:`EXTENT_FRAMES` apart at most."""
        evs = self.ev.events
        j = at if at is not None else self.ev.first_read(last, reader)
        if j is None:
            return last
        by = self.ev.reads_by.get(evs[j][4], [])
        addr, fr = last, evs[j][3]
        for i in by[bisect.bisect_right(by, j) :]:
            e = evs[i]
            if e[3] > fr + EXTENT_FRAMES:
                break
            if e[1] == addr:
                fr = e[3]
            elif addr < e[1] <= addr + 4:
                addr, fr = e[1], e[3]
            else:
                break
        return addr + (self.result.unit - 1)

    def _first_reader(self, offset: int) -> int | None:
        i = self.ev.first_read(offset)
        return None if i is None else self.ev.events[i][4]

    def _ram_code(self, p: ProbeServer, v: int, k: int, res) -> Code:
        """What replaced output ``k`` when a code became ``v``."""
        if res is None:
            return Code(v, "stall" if p.answer is None else "same")
        refv, got = res
        kk = k - (len(p.ref_values) - len(refv))
        out = replaced(refv, got, kk)
        line = line_break(refv, got, kk) if out is None else None
        if line is not None:
            return Code(v, "line", [line])
        if out is None:
            return Code(v, edits_kind(refv, got))
        kind = "char" if len(out) == 1 else "empty" if not out else "string"
        return Code(v, kind, list(out))

    def _ram_codes(self, p: ProbeServer, src) -> Step[None]:
        """Every value of a source byte used once, and what the engine writes
        in its output's place."""
        evs, r = self.ev.events, self.result
        items = sorted(src.items())
        used_once = [(k, v) for k, v in items if v[2] == "copied" and v[1] == [k]]
        copied = [(k, v) for k, v in items if v[2] == "copied"]
        k, s = (used_once or copied or items)[0]
        b, j = s[0], s[3]
        fr = evs[j][3]
        r.code_byte = b
        if r.unit > 1:
            r.notes.append(f"only the first byte of each {r.unit}-byte code was swept")
        for v in range(256):
            self._at(f"codes: value {v + 1} of 256", v, 256)
            writes, subs = self._change(p, b, j, v)
            res = yield from p.effect(writes, fr, subs)
            r.codes.append(self._ram_code(p, v, k, res))
            self._count()

    def _ram_stream(self, p: ProbeServer, occ_w, widx, span) -> Step[None]:
        """Text with no ROM source may be read from a RAM buffer the game
        decoded it into: an earlier write outside the text — of the first
        output's value first — whose reads decide that output. Its codes are then
        swept there — each value substituted for what the reader reads."""
        evs, r, console = self.ev.events, self.result, self.console
        lo, hi = min(span), max(span)
        i = occ_w[0]
        k = widx.index(i)
        value, f_out = evs[i][2], evs[i][3]
        same, other, seen = [], [], {}
        for j in range(i - 1, -1, -1):
            e = evs[j]
            if e[3] < f_out - WINDOWS[2] or len(seen) >= 16 * RAM_CANDIDATES:
                break
            if e[0] == "W" and not lo <= e[1] <= hi and e[1] not in seen:
                seen[e[1]] = j
                (same if e[2] == value else other).append(j)
        # The text's first output is read from the start of a buffer: the
        # first address of each run written comes before the rest.
        starts = [j for j in other if evs[j][1] - 1 not in seen]
        rest = [j for j in other if evs[j][1] - 1 in seen]
        cands = (same + starts + rest)[:RAM_CANDIDATES]
        for n, j in enumerate(cands):
            self._at(f"RAM buffer: candidate {n + 1} of {len(cands)}", n, len(cands))
            try:
                mem, off = console.ram_of(evs[j][1])
            except ValueError:
                continue
            nth = self._write_count(p, evs[j][1], j)

            def sub(v, mem=mem, off=off, nth=nth):
                return (ReadSub(mem, off, v, after=nth),)

            res = yield from p.effect([], evs[j][3], sub((evs[j][2] ^ 0x01) & 0xFF))
            self._count()
            if res is None:
                continue
            refv, got = res
            kk = k - (len(p.ref_values) - len(refv))
            if not (0 <= kk < min(len(got), len(refv))):
                continue
            if got[:kk] != refv[:kk] or got[kk] == refv[kk]:
                continue
            r.code_ram = (mem, off)
            r.notes.append(f"the text is read from a RAM buffer: {mem} ${off:X}")
            for v in range(256):
                self._at(f"codes: value {v + 1} of 256", v, 256)
                res = yield from p.effect([], evs[j][3], sub(v))
                r.codes.append(self._ram_code(p, v, k, res))
                self._count()
            return

    def _write_count(self, p: ProbeServer, a: int, j: int) -> int:
        """How many writes to RAM address ``a`` the run from the savestate a
        probe of event ``j`` starts at makes, up to and with ``j``."""
        evs = self.ev.events
        si = p.state_for(evs[j][3]) or 1
        start = p.states[si - 1] if p.states else 0
        lo = bisect.bisect_left(self.ev.frames, start, 0, j)
        return sum(1 for x in range(lo, j + 1) if evs[x][0] == "W" and evs[x][1] == a)

    def _stream(self, p: ProbeServer, widx, src, occ_w, f0, f1) -> Step[None]:
        """The packed stream and the bit layouts that fit it."""
        evs, rom, r = self.ev.events, self.rom, self.result
        frames = self.ev.frames
        first: dict[int, int] = {}
        for x in range(
            bisect.bisect_left(frames, f0 - 1), bisect.bisect_right(frames, f1)
        ):
            e = evs[x]
            if e[0] == "E" and e[1] not in first:
                first[e[1]] = e[3]
        self._at(f"stream: which of {len(first)} bytes change the text")
        dep = yield from self._find(p, list(first), first)
        firstk: dict[int, int] = {}
        for n, a in enumerate(dep):
            self._at(f"stream: byte {n + 1} of {len(dep)}", n, len(dep))
            res = yield from p.effect([(a, rom[a] ^ 0x01)], first[a])
            if res:
                refv, got = res
                off = len(p.ref_values) - len(refv)
                m = min(len(got), len(refv))
                x = next((x for x in range(m) if got[x] != refv[x]), m)
                firstk[a] = x + off
        runs: list[list[int]] = []
        cur = None
        for a in dep:
            if a not in firstk:
                continue
            if cur and a == cur[-1] + 1 and firstk[a] >= firstk[cur[-1]]:
                cur.append(a)
            else:
                cur = [a]
                runs.append(cur)
        if not runs:
            r.notes.append("no stream was found for the packed text")
            return
        runs.sort(key=lambda run: -len({firstk[a] for a in run}))
        s0, s1 = runs[0][0], runs[0][-1]
        r.stream = (s0, s1)
        pos = {i: k for k, i in enumerate(widx)}
        tokens: list = []
        for i in occ_w:
            k = pos[i]
            if k in src and src[k][2] == "copied":
                tokens.append(src[k][0])
            else:
                tokens.append(("value", evs[i][2]))
        r.tokens = [list(t) if isinstance(t, tuple) else t for t in tokens]
        self._at("stream: fitting bit layouts")
        # The run and a byte either side.
        lo = max(0, s0 - 1)
        lays = yield from bitlayout.iter_fits(tokens, rom[lo : s1 + 2], rom.__getitem__)
        r.layouts = [layout_json(lay) | {"from": lo} for lay in lays]
        if not lays:
            r.notes.append(
                "the layout search ran out of budget"
                if getattr(lays, "exhausted", 0)
                else "the stream fits no bit layout"
            )

    def _find(self, p: ProbeServer, cands, first) -> Step[list[int]]:
        """Every candidate whose change alone changes the output, by halving:
        a set that changes nothing holds none. A set whose probe got no
        answer is halved all the same; a single byte of no answer is noted."""
        rom = self.rom
        found = []
        stack = [sorted(cands, key=lambda a: (first[a], a))]
        while stack:
            s = stack.pop()
            changed = yield from p.test([(a, rom[a] ^ 0x01) for a in s], first[s[0]])
            if changed is False:
                continue
            if len(s) == 1:
                if changed:
                    found.append(s[0])
                else:
                    self.result.notes.append(f"the stream byte ${s[0]:X} got no answer")
            else:
                h = len(s) // 2
                stack += [s[h:], s[:h]]
            self._count()
        return sorted(found)

    # -- pointers

    def _pointers(
        self, first_src: int, own: frozenset, reader: int | None
    ) -> Step[None]:
        """The bytes that hold the string's address, by the change-by-2 test."""
        evs, rom, r, console = self.ev.events, self.rom, self.result, self.console
        i0 = self.ev.first_read(first_src)
        if i0 is None:
            r.notes.append("the string's first byte was never read")
            return
        reader = evs[i0][4] if reader is None else reader
        if console.id == "gb":
            r.follows_call = gb_follows_call(rom, first_src, reader)
        lo, hi = max(0, first_src - 0x8000), min(len(rom) - 1, first_src + 0x8000)
        rfrom = evs[i0][3]
        self._at("pointers: starting the probe server")
        p = self._server(
            name="pointers",
            robs=(lo, hi, reader, rfrom, reader_cpu(console, reader), 4),
            pend=rfrom + 1,
        )
        yield from p.start()
        cands, seen = [], set()
        for j in range(i0 - 1, -1, -1):
            e = evs[j]
            if e[0] == "E" and e[1] not in seen and e[1] not in own:
                seen.add(e[1])
                cands.append((e[1], e[3], "read before", None))
            if len(cands) >= POINTER_READS:
                break
        cands += self._static_pointers(first_src, seen, evs[0][3])
        yield from p.effect([], rfrom)
        ans = p.answer
        base = ans.offsets[0] if ans and ans.offsets else None
        r.first_read = base
        if base is None:
            r.notes.append("the string's first read could not be measured")
            self._call_note()
            return
        if r.string and 0 < r.string[0] - base <= STEP:
            # The string's first byte came before its first traced source: a
            # code whose output was a dictionary's.
            r.string = (base, r.string[1])
        probed = {c[0] for c in cands}
        n = 0
        while n < len(cands):
            b, fr, why, bank = cands[n]
            self._at(f"pointers: candidate {n + 1} of {len(cands)}", n, len(cands))
            n += 1
            # 2, so a halfword or word reader still moves.
            c = 2 if rom[b] < 0xFE else -2
            yield from p.effect([(b, rom[b] + c)], fr)
            ans = p.answer
            self._count()
            if ans is None:
                r.stalls.append(b)
                continue
            if not ans.offsets:
                r.strays.append(b)
                continue
            d = ans.offsets[0] - base
            want = tuple(m * c for m in MOVES)
            if why == "bank":
                want = (bank,)
            if d not in want or d == 0:
                continue
            static = why in ("holds the address", "above", "bank")
            # Only the address's own low byte follows the instruction.
            named = why == "holds the address" and console.id in OPERAND_CONSOLES
            note = gb_operand(rom, b, console.id == "sms") if named else ""
            r.pointers.append(
                Pointer(b, d, "holds the address" if static else why, note)
            )
            if why == "holds the address" and len(cands) < POINTER_BUDGET:
                # The byte above, and the bank byte after a 16-bit address.
                if b + 1 not in probed and b + 1 < len(rom):
                    probed.add(b + 1)
                    cands.append((b + 1, fr, "above", None))
                if bank is not None and b + 2 not in probed and b + 2 < len(rom):
                    probed.add(b + 2)
                    cands.append((b + 2, fr, "bank", bank))
        self._call_note()
        if console.id == "gba":
            yield from self._copies()

    def _call_note(self) -> None:
        """Say the string follows a call, once no pointer was found."""
        r = self.result
        if r.follows_call is not None and not r.pointers:
            r.notes.append(
                f"the string follows a call at ${r.follows_call:X}: the call site "
                "is its reference, and no pointer holds it"
            )

    def _copies(self) -> Step[None]:
        """Every other word holding a confirmed GBA pointer's value."""
        r, rom = self.result, self.rom
        known = self.ev.touched_known
        done = set()
        for p in r.pointers:
            b = p.address
            if abs(p.moves) not in (2, 4, 8) or b % 4 or b + 4 > len(rom):
                continue
            value = rom[b : b + 4]
            if value in done or not _gba_pointer(int.from_bytes(value, "little")):
                continue
            done.add(value)
            self._at("pointers: other copies of the pointer")
            r.copies += yield from gba_copies(
                rom, b, self.ev.touched if known else None
            )

    def _static_pointers(self, first_src: int, seen: set, start: int) -> list:
        """The places in the ROM holding the string's address that this
        replay read or executed: its 4-byte form, or its low 16 bits once,
        noting when the bank the address is in follows."""
        rom, console = self.rom, self.console
        buses = []
        spelled = self.ev.bus_of.get(first_src)
        for bus in ([spelled] if spelled is not None else []) + console.to_bus(
            first_src
        ):
            if bus not in buses:
                buses.append(bus)
        pats: dict[bytes, dict[int, int]] = {}
        for bus in buses:
            if bus > 0xFFFFFF:
                pats.setdefault(bus.to_bytes(4, "little"), {})
            else:
                banks = pats.setdefault((bus & 0xFFFF).to_bytes(2, "little"), {})
                c = 2 if (bus >> 16) < 0xFE else -2
                try:
                    move = console.to_rom(bus + (c << 16)) - first_src
                except (ValueError, IndexError):
                    move = 0
                banks[bus >> 16] = move
        out = []
        for pat, banks in pats.items():
            k = rom.find(pat)
            while k >= 0 and len(out) < POINTER_BUDGET:
                # On the Game Boy an address in $4000-$7FFF means the bank
                # switched in: only code in bank 0 or in the string's own
                # bank reaches the string by it.
                fits = console.id != "gb" or gb_bank_fits(rom, k, first_src)
                if fits and k not in seen and self.ev.touched(k):
                    seen.add(k)
                    nxt = rom[k + 2] if k + 2 < len(rom) else None
                    out.append((k, start, "holds the address", banks.get(nxt) or None))
                k = rom.find(pat, k + 1)
        return out

    # -- VRAM output

    def _vram(self) -> Step[None]:
        evs, occ, r, rom, console = (
            self.ev.events,
            self.occ,
            self.result,
            self.rom,
            self.console,
        )
        reader = occ.pc
        chars = occ.letters
        r.output, r.how, r.reader = "vram", occ.how, reader
        r.decoder, r.gaps = dict(occ.decoder), occ.gaps
        r.outputs = len(chars)
        lo = min(evs[i][1] for i, _ in chars)
        f_read = evs[chars[0][0]][3]
        r.frames = (f_read, self.moment.frame)
        r.unit = vram_unit(occ.unit, [evs[i][2] for i, _ in chars])
        cpu = reader_cpu(console, reader)
        self._at("starting the probe server")
        p = self._server(
            name="sources",
            vobs=(console.vram, self.moment.frame),
            pend=self.moment.frame,
            robs=(
                max(0, lo - 0x8000),
                min(len(rom) - 1, lo + 0x8000),
                reader,
                f_read,
                cpu,
            ),
        )
        yield from p.start()
        cells = []
        for n, (i, chr_) in enumerate(chars):
            self._at(f"sources: character {n + 1} of {len(chars)}", n, len(chars))
            b, fr = evs[i][1], evs[i][3]
            yield from p.effect([(b, rom[b] ^ 0x01)], fr)
            ans = p.answer
            if ans and ans.vram and ans.vram[0]:
                cells.append((chr_, b, ans.vram[1], ans.vram[2], fr, i))
                r.sources.append(Source(n, b, "vram", [ans.vram[1], ans.vram[2]]))
                r.typed.append((chr_, evs[i][2], b))
                if p.control is None:
                    p.control = ([(b, rom[b] ^ 0x01)], fr)
            else:
                r.typed.append((chr_, evs[i][2], None))
            self._count()
        if not cells:
            r.notes.append("no typed character changed VRAM")
            return
        last = max(cells, key=lambda c: c[1])
        r.string = (min(c[1] for c in cells), self._extent(last[1], reader, at=last[5]))
        vlo, vhi = min(c[2] for c in cells), max(c[3] for c in cells)
        self._at("settling")
        r.settled = yield from p.settle(vlo, vhi, f_read)
        yield from p.move_vobs(r.settled)
        # From here a probe compares the text's VRAM once it holds still, no
        # sooner after the changed byte's read than the reference took.
        wait = max(0, r.settled - cells[0][4])
        yield from p.window(
            vlo, vhi, STABLE_FRAMES, wait, max(STABLE_DEADLINE, 2 * wait)
        )
        yield from self._pointers(lo, frozenset(evs[i][1] for i, _ in chars), None)
        yield from self._vram_codes(p, cells, vlo, vhi)

    def _vram_codes(self, p: ProbeServer, cells, vlo: int, vhi: int) -> Step[None]:
        rom, r = self.rom, self.result
        # The reader's reads from its first read of the swept byte on: the
        # read after it says what the code was.
        cell = cells[0]
        yield from p.reads_from(cell[1])
        yield from p.effect([], cell[4])
        ref_reads = p.answer.offsets if p.answer else []
        if cell[1] not in ref_reads:
            cell = next((c for c in cells[1:] if c[1] in ref_reads), None)
            if cell is None:
                r.notes.append("the reader's reads do not reach the typed text")
                return
        _, b0, clo, chi, fr, i0 = cell
        j = ref_reads.index(b0)
        # Text drawn a cell a character: each typed character's change landed
        # in a span of its own, all as wide. There a printable code changes
        # its own cell and nothing else.
        spans = sorted((c[2], c[3]) for c in cells)
        fixed = len({hi - lo for lo, hi in spans}) == 1 and all(
            a[1] < b[0] for a, b in zip(spans, spans[1:], strict=False)
        )

        def nxt(rs):
            return next((a for a in rs[j + 1 :] if a != b0), None)

        ref_next = nxt(ref_reads)
        r.code_byte = b0
        unit = r.unit

        def classify(code: int, ans) -> Code:
            if ans is None:
                return Code(code, "stall")
            rs = ans.offsets
            n_ = nxt(rs) if len(rs) > j and rs[j] == b0 else None
            if n_ is None:
                return Code(code, "end")
            if ref_next is not None and n_ != ref_next:
                return Code(code, "command", skip=n_ - ref_next)
            d = ans.vram[3] if ans.vram else {}
            d = {a: v for a, v in d.items() if vlo <= a <= vhi}
            if fixed and any(not clo <= a <= chi for a in d):
                # The reader went on as before, but what was drawn after the
                # character changed: the text ended there, and the caller
                # drew on from the next byte (or drew nothing more).
                return Code(code, "end")
            digest = hashlib.md5(repr(sorted(d.items())).encode()).hexdigest()
            return Code(code, "printable", image=digest[:8])

        # A wide code's bytes: low first, or (big-endian) high first.
        order = getattr(self.occ, "endian", "little")

        def shift(byte: int) -> int:
            return 8 * (byte if order == "little" else unit - 1 - byte)

        base = int.from_bytes(rom[b0 : b0 + unit], order)
        for byte in range(unit):
            for v in range(256):
                self._at(
                    f"codes: byte {byte + 1} of {unit}, value {v + 1} of 256",
                    byte * 256 + v,
                    unit * 256,
                )
                code = (base & ~(0xFF << shift(byte))) | (v << shift(byte))
                if unit == 1:
                    writes, subs = self._change(p, b0, i0, v)
                else:
                    writes, subs = [(b0 + byte, v)], ()
                yield from p.effect(writes, fr, subs)
                r.codes.append(classify(code, p.answer))
                self._count()
        # A code wider than a byte is swept a byte at a time; the one the
        # reader stopped at is tried whole.
        last = r.string[1] - unit + 1
        term = int.from_bytes(rom[last : last + unit], order)
        if unit > 1 and all(c.value != term for c in r.codes):
            self._at("codes: the code the string ends with")
            writes = [(b0 + k, (term >> shift(k)) & 0xFF) for k in range(unit)]
            yield from p.effect(writes, fr)
            r.codes.append(classify(term, p.answer))
