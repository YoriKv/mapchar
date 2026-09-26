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
  place in the ROM holding its address that this replay read or executed.
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
long a search goes on, and past it a thing is reported not found.
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
from mapchar.capture.probe import ProbeServer, reader_cpu
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


@dataclass
class Pointer:
    address: int
    """ROM offset of the byte."""
    moves: int
    """How far changing it moved the string's first read."""
    why: str
    """``read before`` or ``holds the address``."""


@dataclass
class Code:
    value: int
    kind: str
    """RAM output: ``char``, ``string``, ``empty``, ``same``, ``line`` (the
    character in :attr:`out`, then the line ends) or the shape of a structural
    change (``truncate``, ``extend``, ``shift``, ``rewrite``).
    VRAM output: ``printable``, ``command`` or ``end``; ``stall`` when the
    probe got no answer."""
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
    """Pointer candidates whose change stalled the game before the string."""
    code_byte: int | None = None
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
        r.codes = [Code(**c) for c in d.get("codes", [])]
        for key in ("region", "frames", "string", "stream"):
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

    def __init__(self, evs):
        self.evs = evs
        self.frames = [e[3] for e in evs]
        self.sorted = all(map(le, self.frames, self.frames[1:]))
        self.at = 0
        self.last: dict[int, int] = {}
        self.byval: dict[int, dict[int, int]] = defaultdict(dict)

    def window(self, i, back, tried, value):
        """``(same, other)``: the bytes read before ``evs[i]`` down to its
        frame less ``back`` (None: all), not in ``tried``, whose latest read
        gave ``value`` / did not (at most :data:`OTHER_BUDGET` of those), as
        ``(byte, frame of that read)``."""
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
                same.append((b, evs[j][3]))
        for b, j in reversed(last.items()):
            if j < lo:
                break
            if b in tried:
                continue
            e = evs[j]
            if e[2] != value:
                other.append((b, e[3]))
                if len(other) == OTHER_BUDGET:
                    break
        return same, other

    def _walk(self, i, back, tried, value):
        evs = self.evs
        seen, same, other = set(), [], []
        for j in range(i - 1, -1, -1):
            e = evs[j]
            if back is not None and e[3] < evs[i][3] - back:
                break
            if e[0] == "E" and e[1] not in seen:
                seen.add(e[1])
                if e[1] not in tried:
                    (same if e[2] == value else other).append((e[1], e[3]))
        return same, other[:OTHER_BUDGET]


def advancing(sources: list[int]) -> float:
    """The share of outputs whose source lies just past the stream's last,
    walking the outputs in order: near 1 for text read from one stream,
    whatever it expands on the way; near 0 for text copied from a
    dictionary."""
    if len(sources) < 2:
        return 1.0
    last, n = sources[0], 0
    for b in sources[1:]:
        if last < b <= last + STEP:
            n += 1
            last = b
    return n / (len(sources) - 1)


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
    for n in range(0, len(got) - k + 1):
        tail = got[k + n :]
        m = min(len(tail), len(rest))
        if m and tail[:m] == rest[:m]:
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


# -- the tracer


class Tracer:
    """Traces one capture. :meth:`run` is a step; :attr:`status` says what it
    is on."""

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
        self._servers: list[ProbeServer] = []

    def _server(self, **kw) -> ProbeServer:
        p = ProbeServer(self.emulator, self.rom_path, self.console, self.folder, **kw)
        self._servers.append(p)
        return p

    def close(self) -> None:
        for p in self._servers:
            p.close()
        self._servers.clear()

    def _count(self) -> None:
        self.result.probes = sum(p.n for p in self._servers)
        self.result.no_answer = sum(p.no_answer for p in self._servers)

    def run(self) -> Step[Result]:
        try:
            if self.occ.kind == "ram":
                yield from self._ram()
            else:
                yield from self._vram()
        finally:
            self._count()
            self.close()
        return self.result

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
        self.status = "starting the probe server"
        p = self._server(
            name="sources", obs=(mt, min(offs), max(offs)), obs_from=f0, obs_to=f1
        )
        yield from p.start()
        widx, src = yield from self._sources(p, occ_w, span, f0)
        pos = {i: k for k, i in enumerate(widx)}
        order_pos = {i: n for n, i in enumerate(occ_w)}
        r.sources = [
            Source(order_pos[widx[k]], s[0], s[2], s[1]) for k, s in sorted(src.items())
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
            return
        firsts = sorted(src)
        r.packed = advancing([src[k][0] for k in firsts]) < PACKED
        r.string = (src[firsts[0]][0], self._extent(src[firsts[-1]][0]))
        first_src, reader = src[firsts[0]][0], None
        if r.packed:
            yield from self._stream(p, widx, src, occ_w, f0, f1)
            if r.stream:
                first_src = r.stream[0]
                reader = self._first_reader(first_src)
        own = frozenset(s[0] for s in src.values())
        yield from self._pointers(first_src, own, reader)
        if not r.packed:
            yield from self._ram_codes(p, src, widx)

    def _sources(self, p: ProbeServer, occ_w, span, f0) -> Step[tuple[list, dict]]:
        evs = self.ev.events
        lo, hi = min(span), max(span)
        f1 = evs[occ_w[-1]][3]
        reads = Reads(evs)
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
        src: dict[int, tuple[int, list[int], str]] = {}
        prev = None
        for n, i in enumerate(occ_w):
            self.status = f"sources: output {n + 1} of {len(occ_w)}"
            k = pos[i]
            best, tried = None, set()
            for back in WINDOWS:
                same, other = reads.window(i, back, tried, evs[i][2])
                cands = same + other
                if prev is not None:
                    cands.sort(key=lambda c, prev=prev: c[0] != prev + 1)
                tried |= {c[0] for c in cands}
                best = yield from self._tier(p, cands, k, best)
                if best:
                    break
            if best:
                src[k] = best
                prev = best[0]
            else:
                self.result.notes.append(f"output {n} has no source")
            self._count()
        return widx, src

    def _tier(self, p: ProbeServer, cands, k, best) -> Step:
        rom = self.rom
        for b, fr in cands:
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
                return (b, changed, "copied")
            if len(got) == len(refv):
                changed = [x + off for x in range(len(got)) if got[x] != refv[x]]
                if k in changed and (best is None or best[2] == "determines"):
                    best = (b, changed, "only-this")
            if best is None and first is not None and first + off == k:
                best = (b, [k], "determines")
        return best

    def _extent(self, last: int, reader: int | None = None) -> int:
        """The string's last byte: the reader of its last source goes on
        reading past it, in order, to the end token or the string's end."""
        evs = self.ev.events
        j = max(
            (
                i
                for i, e in enumerate(evs)
                if e[0] == "E" and e[1] == last and (reader is None or e[4] == reader)
            ),
            default=None,
        )
        if j is None:
            return last
        pc, at = evs[j][4], last
        for e in evs[j + 1 : j + 4096]:
            if e[0] != "E" or e[4] != pc or e[1] == at:
                continue
            if at < e[1] <= at + 4:
                at = e[1]
            else:
                break
        return at + (self.result.unit - 1)

    def _first_reader(self, offset: int) -> int | None:
        for e in self.ev.events:
            if e[0] == "E" and e[1] == offset:
                return e[4]
        return None

    def _ram_codes(self, p: ProbeServer, src, widx) -> Step[None]:
        """Every value of a source byte used once, and what the engine writes
        in its output's place."""
        evs, r = self.ev.events, self.result
        items = sorted(src.items())
        used_once = [(k, v[0]) for k, v in items if v[2] == "copied" and v[1] == [k]]
        copied = [(k, v[0]) for k, v in items if v[2] == "copied"]
        k, b = (used_once or copied or [(items[0][0], items[0][1][0])])[0]
        fr = next(e[3] for e in evs if e[0] == "E" and e[1] == b)
        r.code_byte = b
        for v in range(256):
            self.status = f"codes: value {v + 1} of 256"
            res = yield from p.effect([(b, v)], fr)
            if res is None:
                r.codes.append(Code(v, "same"))
                continue
            refv, got = res
            kk = k - (len(p.ref_values) - len(refv))
            out = replaced(refv, got, kk)
            line = line_break(refv, got, kk) if out is None else None
            if line is not None:
                r.codes.append(Code(v, "line", [line]))
            elif out is None:
                r.codes.append(Code(v, edits_kind(refv, got)))
            else:
                kind = "char" if len(out) == 1 else "empty" if not out else "string"
                r.codes.append(Code(v, kind, list(out)))
            self._count()

    def _stream(self, p: ProbeServer, widx, src, occ_w, f0, f1) -> Step[None]:
        """The packed stream and the bit layouts that fit it."""
        evs, rom, r = self.ev.events, self.rom, self.result
        first: dict[int, int] = {}
        for e in evs:
            if e[0] == "E" and f0 - 1 <= e[3] <= f1 and e[1] not in first:
                first[e[1]] = e[3]
        self.status = f"stream: which of {len(first)} bytes change the text"
        dep = yield from self._find(p, list(first), first)
        firstk: dict[int, int] = {}
        for n, a in enumerate(dep):
            self.status = f"stream: byte {n + 1} of {len(dep)}"
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
        self.status = "stream: fitting bit layouts"
        # The run and a byte either side.
        lays = yield from bitlayout.iter_fits(
            tokens, rom[s0 - 1 : s1 + 2], rom.__getitem__
        )
        r.layouts = [layout_json(lay) | {"from": s0 - 1} for lay in lays]

    def _find(self, p: ProbeServer, cands, first) -> Step[list[int]]:
        """Every candidate whose change alone changes the output, by halving:
        a set that changes nothing holds none."""
        rom = self.rom
        found = []
        stack = [sorted(cands, key=lambda a: (first[a], a))]
        while stack:
            s = stack.pop()
            changed = yield from p.test([(a, rom[a] ^ 0x01) for a in s], first[s[0]])
            if not changed:
                continue
            if len(s) == 1:
                found.append(s[0])
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
        i0 = next(
            (i for i, e in enumerate(evs) if e[0] == "E" and e[1] == first_src), None
        )
        if i0 is None:
            r.notes.append("the string's first byte was never read")
            return
        reader = evs[i0][4] if reader is None else reader
        lo, hi = max(0, first_src - 0x8000), min(len(rom) - 1, first_src + 0x8000)
        rfrom = evs[i0][3]
        self.status = "pointers: starting the probe server"
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
                cands.append((e[1], e[3], "read before"))
            if len(cands) >= POINTER_READS:
                break
        start = evs[0][3]
        for bus in console.to_bus(first_src):
            pats = (
                [bus.to_bytes(4, "little")]
                if bus > 0xFFFFFF
                else [bus.to_bytes(3, "little"), (bus & 0xFFFF).to_bytes(2, "little")]
            )
            for pat in pats:
                k = rom.find(pat)
                while k >= 0 and len(cands) < POINTER_BUDGET:
                    if k not in seen and self.ev.touched(k):
                        seen.add(k)
                        cands.append((k, start, "holds the address"))
                    k = rom.find(pat, k + 1)
        yield from p.effect([], rfrom)
        ans = p.answer
        base = console.to_rom(ans.reads[0]) if ans and ans.reads else None
        r.first_read = base
        if base is None:
            r.notes.append("the string's first read could not be measured")
            return
        for n, (b, fr, why) in enumerate(cands):
            self.status = f"pointers: candidate {n + 1} of {len(cands)}"
            c = (
                2 if rom[b] < 0xFE else -2
            )  # 2, so a halfword or word reader still moves
            yield from p.effect([(b, rom[b] + c)], fr)
            ans = p.answer
            if ans is None or not ans.reads:
                r.stalls.append(b)
                continue
            d = console.to_rom(ans.reads[0]) - base
            if d in tuple(m * c for m in MOVES):
                r.pointers.append(Pointer(b, d, why))
            self._count()

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
        r.unit = 2 if any(evs[i][2] > 0xFF for i, _ in chars) else 1
        cpu = reader_cpu(console, reader)
        self.status = "starting the probe server"
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
            self.status = f"sources: character {n + 1} of {len(chars)}"
            b, fr = evs[i][1], evs[i][3]
            yield from p.effect([(b, rom[b] ^ 0x01)], fr)
            ans = p.answer
            if ans and ans.vram and ans.vram[0]:
                cells.append((chr_, b, ans.vram[1], ans.vram[2]))
                r.sources.append(Source(n, b, "vram", [ans.vram[1], ans.vram[2]]))
                r.typed.append((chr_, evs[i][2], b))
            else:
                r.typed.append((chr_, evs[i][2], None))
            self._count()
        if not cells:
            r.notes.append("no typed character changed VRAM")
            return
        r.string = (
            min(c[1] for c in cells),
            self._extent(max(c[1] for c in cells), reader),
        )
        vlo, vhi = min(c[2] for c in cells), max(c[3] for c in cells)
        self.status = "settling"
        r.settled = yield from p.settle(vlo, vhi, f_read)
        yield from p.move_vobs(r.settled)
        yield from self._pointers(lo, frozenset(evs[i][1] for i, _ in chars), None)
        yield from self._vram_codes(p, cells)

    def _vram_codes(self, p: ProbeServer, cells) -> Step[None]:
        evs, rom, r, console = self.ev.events, self.rom, self.result, self.console
        _, b0, *_ = cells[0]
        fr = next(e[3] for e in evs if e[0] == "E" and e[1] == b0)
        yield from p.effect([], fr)
        ref_reads = [console.to_rom(a) for a in (p.answer.reads if p.answer else [])]
        j = ref_reads.index(b0) if b0 in ref_reads else 0

        def nxt(rs):
            return next((a for a in rs[j + 1 :] if a != b0), None)

        ref_next = nxt(ref_reads)
        r.code_byte = b0
        unit = r.unit

        def classify(code: int, ans) -> Code:
            if ans is None:
                return Code(code, "stall")
            rs = [console.to_rom(a) for a in ans.reads]
            n_ = nxt(rs) if len(rs) > j and rs[j] == b0 else None
            if n_ is None:
                return Code(code, "end")
            if ref_next is not None and n_ != ref_next:
                return Code(code, "command", skip=n_ - ref_next)
            d = ans.vram[3] if ans.vram else {}
            digest = hashlib.md5(repr(sorted(d.items())).encode()).hexdigest()
            return Code(code, "printable", image=digest[:8])

        base = int.from_bytes(rom[b0 : b0 + unit], "little")
        for byte in range(unit):
            for v in range(256):
                self.status = f"codes: byte {byte + 1} of {unit}, value {v + 1} of 256"
                code = (base & ~(0xFF << 8 * byte)) | (v << 8 * byte)
                yield from p.effect([(b0 + byte, v)], fr)
                r.codes.append(classify(code, p.answer))
                self._count()
        # A code wider than a byte is swept a byte at a time; the one the
        # reader stopped at is tried whole.
        last = r.string[1] - unit + 1
        term = int.from_bytes(rom[last : last + unit], "little")
        if unit > 1 and all(c.value != term for c in r.codes):
            self.status = "codes: the code the string ends with"
            writes = [(b0 + k, (term >> 8 * k) & 0xFF) for k in range(unit)]
            yield from p.effect(writes, fr)
            r.codes.append(classify(term, p.answer))
