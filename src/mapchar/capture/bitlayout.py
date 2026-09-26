"""How a bit-packed stream is cut into codes, from a stretch of the stream and
the output it produced.

Each output is the dictionary address it was copied from, or its value when it
was not copied. Every layout of the model — code width, bit order, how many of
the top codes escape to a second code, the start bit — is parsed and aligned
with the outputs by backtracking: a code is one dictionary entry (a run of
consecutive addresses, the same run every time it recurs, no two codes the
same run) or produces nothing. Each sighting gives the layouts that fit it;
:func:`combine` keeps those that fit every sighting with one code table, and a
layout is decided only when one is left. A scheme outside the model — a
Huffman tree — fits nothing, and says so.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Generator
from dataclasses import dataclass

SILENT = "silent"
"""What a code that produces nothing means."""

MAXLEN = 16
"""The longest dictionary entry considered."""

WIDTHS = range(4, 9)
ESCAPES = range(0, 5)
START_BITS = range(0, 24)
ORDERS = ("msb", "lsb")

MEMO = 32
"""A failed search below a decision is remembered once it took this many
alternatives."""

BUDGET = 60000
"""Alternatives one alignment may try before it gives up."""

Output = int | tuple[str, int]
"""A dictionary address, or ``("value", v)`` for an output not copied."""

Meaning = str | tuple
""":data:`SILENT`, or ``(first address, length)``; a value-only entry is
``(("value", v), 1)``."""


@dataclass(frozen=True)
class Layout:
    order: str
    width: int
    escapes: int
    start: int
    table: dict[int, Meaning]
    """Code to what it means."""

    @property
    def key(self) -> tuple[str, int, int]:
        return self.order, self.width, self.escapes

    def code_bits(self, code: int) -> str:
        """The code as the bits the stream holds, first bit first."""
        n = self.width * (2 if code >= 1 << self.width else 1)
        return format(code, f"0{n}b")


def parse(data: bytes, order: str, width: int, escapes: int, start: int) -> list[int]:
    """The codes ``data`` holds under one layout; an escaped code is the
    escape and the code after it as one number."""
    bits = "".join(
        format(b, "08b") if order == "msb" else format(b, "08b")[::-1] for b in data
    )
    pos, codes = start, []
    while pos + width <= len(bits):
        c = int(bits[pos : pos + width], 2)
        pos += width
        if escapes and c >= (1 << width) - escapes and pos + width <= len(bits):
            c = (c << width) | int(bits[pos : pos + width], 2)
            pos += width
        codes.append(c)
    return codes


class _Outputs:
    """What each output position allows, computed once for every layout."""

    def __init__(self, outputs: list[Output], value_at: Callable[[int], int]):
        self.addrs = outputs
        n = len(outputs)
        self.val = [value_at(t) if isinstance(t, int) else t[1] for t in outputs]
        self.value_at = value_at
        runlen = [0] * (n + 1)
        for ps in range(n - 1, -1, -1):
            t = outputs[ps]
            if isinstance(t, int):
                nx = outputs[ps + 1] if ps + 1 < n else None
                runlen[ps] = (
                    1 + runlen[ps + 1] if isinstance(nx, int) and nx == t + 1 else 1
                )
        self.runlen = runlen
        self.opts_at = [
            [(t, 1)]
            if isinstance(t, tuple)
            else [(t, k) for k in range(1, min(runlen[ps], MAXLEN) + 1)]
            for ps, t in enumerate(outputs)
        ]
        # What an entry can still start with, from each position on.
        self.rest = [set(outputs[ps:]) for ps in range(n + 1)]


def align(codes: list[int], o: _Outputs, budget: int = BUDGET) -> dict | None:
    """The code table under which ``codes`` spell the outputs, or None: a
    depth-first search one code at a time over what an unknown code means (the
    longest entry first, then shorter ones, then nothing), giving up after
    ``budget`` alternatives.

    A decision whose whole search failed is remembered with the alternatives it
    took: met again in the same state as far as the rest can tell (the same
    code and output position, the same meanings for the codes still to come,
    the same used entries that could still come), it fails again after as
    many, so they are counted, not searched."""
    addrs, val, runlen, opts_at, rest = o.addrs, o.val, o.runlen, o.opts_at, o.rest
    value_at = o.value_at
    C, P = len(codes), len(addrs)
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for i, c in enumerate(codes):
        first.setdefault(c, i)
        last[c] = i
    live: list = [None] * (C + 1)
    dead: list = [None] * (C + 1)
    m: dict = {}
    used: set = set()
    trail: list = []
    stack: list = []  # [ci, ps, code, alternatives left, trail length, count then]
    memo: dict = {}
    cells: set = set()

    def state(ci, ps):
        if live[ci] is None:
            live[ci] = tuple(c for c in first if first[c] < ci <= last[c])
            dead[ci] = tuple(c for c in first if last[c] < ci)
        rp = rest[ps]
        return (
            ci,
            ps,
            tuple(map(m.__getitem__, live[ci])),
            frozenset(
                v
                for v in map(m.__getitem__, dead[ci])
                if v is not SILENT and v[0] in rp
            ),
        )

    ci = ps = 0
    nodes = 1
    while True:
        ok = True
        while ci < C and ps < P:
            c = codes[ci]
            e = m.get(c)
            if e is not None:
                if e == SILENT:
                    ci += 1
                    continue
                a0, n = e
                if isinstance(a0, tuple):  # known by value: any one-byte entry of it
                    if val[ps] == a0[1]:
                        ci += 1
                        ps += 1
                        continue
                elif (addrs[ps] == a0 and runlen[ps] >= n) or (
                    n == 1
                    and isinstance(addrs[ps], tuple)
                    and value_at(a0) == addrs[ps][1]
                ):
                    ci += 1
                    ps += n
                    continue
                ok = False
                break
            if (ci, ps) in cells:
                got = memo.get(state(ci, ps))
                if got is not None:  # failed before, after this many alternatives
                    nodes += got
                    if nodes > budget:
                        return None
                    ok = False
                    break
            opts = [x for x in opts_at[ps] if x not in used]
            if not opts:
                stack.append([ci, ps, c, [SILENT], len(trail), nodes])
                ok = False
                break
            stack.append([ci, ps, c, [SILENT] + opts[:-1], len(trail), nodes])
            e = opts[-1]
            m[c] = e
            used.add(e)
            trail.append(c)
            ci += 1
            ps += e[1]
        if ok and ps >= P:
            return m
        while stack and not stack[-1][3]:  # decisions whose every alternative failed
            dci, dps, _, _, tl, n0 = stack.pop()
            while len(trail) > tl:
                x = m.pop(trail.pop())
                if x != SILENT:
                    used.discard(x)
            if nodes - n0 >= MEMO:
                memo[state(dci, dps)] = nodes - n0
                cells.add((dci, dps))
        if not stack:
            return None
        ci, ps, c, alts, tl, _ = stack[-1]
        e = alts.pop()
        while len(trail) > tl:
            x = m.pop(trail.pop())
            if x != SILENT:
                used.discard(x)
        nodes += 1
        if nodes > budget:
            return None
        m[c] = e
        trail.append(c)
        ci += 1
        if e != SILENT:
            used.add(e)
            ps += e[1]


def iter_fits(
    outputs: list[Output], data: bytes, value_at: Callable[[int], int]
) -> Generator[None, None, list[Layout]]:
    """Every layout under which ``data``'s codes each mean one dictionary
    entry or nothing; yields between layouts so a caller can pace it."""
    o = _Outputs(outputs, value_at)
    done: dict[tuple, dict | None] = {}
    found: list[Layout] = []
    for order in ORDERS:
        for w in WIDTHS:
            for k in ESCAPES:
                for st in START_BITS:
                    codes = tuple(parse(data, order, w, k, st))
                    if codes not in done:  # the same codes align the same way
                        done[codes] = align(list(codes), o)
                        yield
                    m = done[codes]
                    if m is not None:
                        found.append(Layout(order, w, k, st, dict(m)))
    found.sort(key=lambda f: len(f.table))
    return found


def fits(
    outputs: list[Output], data: bytes, value_at: Callable[[int], int]
) -> list[Layout]:
    gen = iter_fits(outputs, data, value_at)
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def merge(tables: list[dict]) -> dict | None:
    """One code table from several, or None if a code means two things or two
    codes one entry."""
    out: dict = {}
    for m in tables:
        for c, e in m.items():
            if out.setdefault(c, e) != e:
                return None
    ents = [e for e in out.values() if e != SILENT]
    return out if len(ents) == len(set(ents)) else None


@dataclass(frozen=True)
class Decision:
    alive: list[tuple[tuple[str, int, int], list[int], dict]]
    """``(layout key, start bit per sighting, merged table)`` for every
    combination that fits every sighting."""

    @property
    def keys(self) -> list[tuple[str, int, int]]:
        return sorted({a[0] for a in self.alive})

    @property
    def decided(self) -> tuple[tuple[str, int, int], list[int], dict] | None:
        """The one layout left, or None while none or several fit. Layouts
        that read every sighting into the same code table are one: escapes
        no code uses cannot be told apart, and the fewest are kept."""
        if not self.alive:
            return None
        tables = {
            (a[0][0], a[0][1], frozenset(a[2].items()), tuple(a[1])) for a in self.alive
        }
        if len(tables) != 1:
            return None
        return min(self.alive, key=lambda a: a[0][2])


def combine(per_sighting: list[list[Layout]]) -> Decision:
    """The layouts that fit every sighting with one code table."""
    by_key: dict[tuple, list[list[Layout]]] = {}
    for n, layouts in enumerate(per_sighting):
        for lay in layouts:
            slots = by_key.setdefault(lay.key, [[] for _ in per_sighting])
            slots[n].append(lay)
    alive = []
    for key, slots in by_key.items():
        if any(not s for s in slots):
            continue
        for combo in itertools.product(*slots):
            t = merge([lay.table for lay in combo])
            if t is not None:
                alive.append((key, [lay.start for lay in combo], t))
    return Decision(alive)
