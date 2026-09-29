"""Finding the typed text in a replay's evidence.

The text is looked for with :mod:`mapchar.capture.chains` in two kinds of
place: the RAM writes — per writing PC, and the final contents of each run of
addresses — and the ROM reads per reading PC. RAM writes come before ROM
reads, which are for text drawn straight into VRAM. Of the RAM places that
hold all of it, the one holding it earliest is nearest the ROM and is the
occurrence; of the ROM readers, the one reading it in the fewest events, then
the earliest.

Every kind of place is searched a code an event first, RAM before ROM. Only
when that finds nothing anywhere is each searched again as 16-bit codes: every
other write (a tile and its attribute), and neighbouring bytes at consecutive
addresses paired into one value, low byte first or high — a byte-wide bus logs
a 16-bit access as two. Only when that too finds nothing is each searched
backwards, for text stored reversed. A 16-bit search after the plain one keeps
a place that holds the text only every other write — a coprocessor writing each
character twice to one variable — behind a reader that reads it whole.

A bus as wide as the access logs a 16-bit write as one event, its value wider
than a byte: RAM holding the text so gives way to a reader that reads it as a
string (its letters in order, a few bytes apart, not a dictionary's entries
read wherever each lies), since a reader's trace sets every byte of a wide code
and the code the string ends with whole. RAM holding it a byte a code comes
first, the earliest place of all — for packed text, the variable a decoder
writes each character to.

What ends a capture here is said exactly: text with nothing to search for, no
chain anywhere (with the words that did chain, to show which does not), chains
in too many places to tell apart, or the text in RAM at the capture point but
written before the replay began.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from mapchar.capture import chains as ch
from mapchar.capture.evidence import Evidence
from mapchar.capture.protocol import Step

MIN_LETTERS = 4
"""Fewer typed letters than this match too many places to be worth a search."""

TOO_MANY = 40
"""Distinct places holding the whole text past which none can be chosen."""

RUN_MIN = 4
"""Addresses a run of RAM needs before its final contents are searched."""

STRING_STEP = 8
"""The most bytes, per byte of a code, between two neighbouring letters a
reader reads as one string: a separator's codes, a command's parameters."""

PLAIN, WIDE, REVERSED = "plain", "wide", "reversed"
"""The searches, in the order they are tried: a code an event; 16-bit codes;
a code an event, backwards."""


@dataclass
class Occurrence:
    kind: str
    """``ram`` (the text is in RAM writes) or ``rom`` (in a reader's ROM reads)."""
    how: str
    """Where, in words: ``writes by 5B21C``, ``final contents``, ``reads by …``."""
    pc: int | None
    """The writing or reading PC, when one holds it."""
    events: list[int]
    """Evidence indices: the writes from the first matched output to the last
    (``ram``), or the matched letters' reads (``rom``) — of a code two bytes
    wide, both its bytes; of every other write, only the codes'."""
    letters: list[tuple[int, str]]
    """``(evidence index, typed character)`` per matched letter: of a code two
    bytes wide, its first byte's event."""
    order: dict[str, str]
    decoder: dict[int, str]
    """Code to character, from the typed text."""
    gaps: list[tuple[str, list[int]]] = field(default_factory=list)
    """Typed separator and the codes between those two words."""
    unit: int = 1
    """Bytes a code: 1; 2 when two neighbouring byte events make one value or
    one event's value is wider than a byte; 4 when one event's is wider than
    two. The tracer's RAM path takes it as it is; its VRAM path reads 4 as 2."""
    stride: int = 1
    """Events a code steps: 2 when every other write is a code (a tile and its
    attribute). Said for what it is: :attr:`events` already holds only the
    codes' writes, and nothing further reads it."""
    endian: str = "little"
    """How a code two bytes wide was paired: its low byte first or its high."""
    reverse: bool = False
    """The text runs backwards through the events. Said for what it is:
    :attr:`letters` already maps each letter to its event, and nothing further
    reads it."""


@dataclass
class Finding:
    """Why a capture found nothing it can trace."""

    kind: str
    """``no-letters``, ``too-short``, ``no-match``, ``too-many``, ``before``."""
    message: str
    words: list[str] = field(default_factory=list)
    """Every typed word."""
    matched: list[str] = field(default_factory=list)
    """The words the best partial chain holds; the rest did not fit."""
    where: str = ""


def _search_order(text: str) -> dict[str, str]:
    return ch.JIS if ch.has_kana(text) else ch.GOJUON


def check_text(text: str) -> Finding | None:
    """What makes typed text unsearchable before any search."""
    ws = ch.words(text, _search_order(text))
    if not ws:
        return Finding(
            "no-letters",
            "Type the text with kana or Latin letters: nothing else can be "
            "searched for.",
        )
    if sum(len(w) for w in ws) < MIN_LETTERS:
        return Finding(
            "too-short", "Type more of the line: so little text fits too many places."
        )
    return None


class _Best:
    """The partial chain holding the most words, for the no-match report."""

    def __init__(self):
        self.count = 0
        self.ws: list[ch.Word] = []
        self.placed: set[int] = set()

    def see(self, found: list[ch.Chain], ws: list[ch.Word]) -> None:
        for c in found:
            if c.count > self.count:
                self.count = c.count
                self.ws = ws
                self.placed = {k for _, k in c.placed}

    @property
    def words(self) -> list[str]:
        return [self.ws[k].text for k in sorted(self.placed)]

    @property
    def missing(self) -> list[str]:
        """The words the best chain left out, each as often as it was."""
        return [w.text for k, w in enumerate(self.ws) if k not in self.placed]


@dataclass
class _View:
    """A place's events as one sequence of codes."""

    seq: list[int]
    parts: list[tuple[int, ...]]
    """Per code, its events: the first is the code's own."""
    unit: int = 1
    stride: int = 1
    endian: str = "little"

    def at(self, p: int) -> int:
        return self.parts[p][0]


def _views(evs, idx: list[int], mode: str, strided: bool):
    """``idx``'s events as the sequences a search reads: a code an event;
    or, wide, every other event from each phase (``strided``: writes of a
    tile and its attribute) and pairs of neighbouring events at consecutive
    addresses, both phases and both byte orders."""
    if mode != WIDE:
        yield _View([evs[i][2] for i in idx], [(i,) for i in idx])
        return
    if strided:
        for phase in (0, 1):
            sub = idx[phase::2]
            yield _View([evs[i][2] for i in sub], [(i,) for i in sub], stride=2)
    for phase in (0, 1):
        pairs = [(idx[k], idx[k + 1]) for k in range(phase, len(idx) - 1, 2)]
        for endian in ("little", "big"):
            seq = []
            for i, j in pairs:
                lo, hi = (
                    (evs[i][2], evs[j][2])
                    if endian == "little"
                    else (evs[j][2], evs[i][2])
                )
                # Two events at addresses apart are not one access: no code.
                seq.append(lo | hi << 8 if evs[j][1] == evs[i][1] + 1 else ch.VMAX)
            yield _View(seq, pairs, unit=2, endian=endian)


def _search(view: _View, ws, mode: str, best: _Best) -> list[ch.Chain]:
    found = ch.match(view.seq, ws, reverse=mode == REVERSED)
    best.see(found, ws)
    return ch.complete(found, ws)


def _ram(ev: Evidence, text: str, order, best: _Best, mode: str = PLAIN) -> Step[list]:
    evs = ev.events
    writes = [i for i, e in enumerate(evs) if e[0] == "W"]
    by_pc: dict[int, list[int]] = defaultdict(list)
    for i in writes:
        by_pc[evs[i][4]].append(i)
    final = {evs[i][1]: i for i in writes}  # each address's last write
    ws = ch.words(text, order)
    out = []
    for pc, idx in by_pc.items():
        for view in _views(evs, idx, mode, strided=True):
            for c in _search(view, ws, mode, best):
                out.append(_occ("ram", view, c, ws, order, f"writes by {pc:X}", pc))
        yield
    run: list[int] = []
    for a in sorted(final) + [None]:
        if run and (a is None or a != run[-1] + 1):
            if len(run) >= RUN_MIN:
                idx = [final[x] for x in run]
                for view in _views(evs, idx, mode, strided=True):
                    for c in _search(view, ws, mode, best):
                        out.append(
                            _occ("ram", view, c, ws, order, "final contents", None)
                        )
            run = []
        if a is not None:
            run.append(a)
    yield
    return out


def _occ(kind, view: _View, c: ch.Chain, ws, order, how, pc) -> Occurrence:
    """An occurrence: of RAM writes, every event from its first code to its
    last; of ROM reads, the matched letters' events."""
    placed = ch.letters_at(c, ws)
    letters = [(view.at(p), chr_) for p, chr_ in placed]
    if kind == "ram":
        events = sorted({i for p in range(c.start, c.end) for i in view.parts[p]})
    else:
        letters.sort()
        events = sorted({i for p, _ in placed for i in view.parts[p]})
    # One event may carry a code wider than a byte: a bus as wide as the
    # access logs it whole.
    widest = max((view.seq[p] for p, _ in placed), default=0)
    unit = max(view.unit, 4 if widest > 0xFFFF else 2 if widest > 0xFF else 1)
    return Occurrence(
        kind,
        how,
        pc,
        events,
        letters,
        order,
        ch.decoder(view.seq, c, ws, order),
        ch.gaps(view.seq, c, ws),
        unit,
        view.stride,
        view.endian,
        c.reverse,
    )


def _rom(ev: Evidence, text: str, order, best: _Best, mode: str = PLAIN) -> Step[list]:
    evs = ev.events
    by_pc: dict[int, list[int]] = defaultdict(list)
    for i, e in enumerate(evs):
        if e[0] == "E":
            by_pc[e[4]].append(i)
    ws = ch.words(text, order)
    out = []
    for pc, idx in by_pc.items():
        # Repeated reads of one byte count once.
        s = [idx[j] for j in ch.changes([evs[i][1] for i in idx])]
        for view in _views(evs, s, mode, strided=False):
            for c in _search(view, ws, mode, best):
                out.append(_occ("rom", view, c, ws, order, f"reads by {pc:X}", pc))
        yield
    return out


def _in_ram_now(ev: Evidence, text: str, order) -> str | None:
    """Where the text sits in RAM at the capture point, 1 or 2 bytes a code."""
    ws = ch.words(text, order)
    names = ev.console.rams[:-1] + tuple(getattr(ev.console, "extra_rams", ()))
    for name in names:
        data = ev.ram(name)
        if not data:
            continue
        for width in (1, 2):
            for phase in range(width):
                seq = (
                    list(data)
                    if width == 1
                    else [
                        data[i] | data[i + 1] << 8
                        for i in range(phase, len(data) - 1, 2)
                    ]
                )
                found = ch.complete(ch.match(seq, ws, limit=4), ws)
                if found:
                    return f"{name} ${found[0].start * width + phase:X}"
    return None


def find(ev: Evidence, text: str) -> Step[tuple[Occurrence | None, Finding | None]]:
    """The occurrence of ``text``, or why there is none."""
    bad = check_text(text)
    if bad:
        return None, bad
    best = _Best()
    orders = ch.orders_for(text)

    def search(kind: str, mode: str) -> Step[list[Occurrence]]:
        """The places of one kind holding the text, by one search."""
        found: list[Occurrence] = []
        for order in orders:
            found += yield from (_ram if kind == "ram" else _rom)(
                ev, text, order, best, mode
            )
        return found

    for mode in (PLAIN, WIDE, REVERSED):
        ram = yield from search("ram", mode)
        narrow = [o for o in ram if o.unit == 1]
        if narrow:
            return _chosen(ev, text, narrow)
        rom = yield from search("rom", mode)
        if ram:  # codes wider than a byte: a reader of the string first
            rom = [o for o in rom if _in_order(ev, o)]
        if rom or ram:
            return _chosen(ev, text, rom or ram)
    words = [w.text for w in ch.words(text, orders[-1])]
    for order in orders:
        where = _in_ram_now(ev, text, order)
        yield
        if where:
            return None, Finding(
                "before",
                "The text is in RAM, but it was drawn before the replay began: "
                "pause sooner after it appears next time, or give its font in the "
                "capture setup.",
                words,
                words,
                where,
            )
    if best.count:
        words = [w.text for w in best.ws]
        return None, Finding(
            "no-match",
            "These words do not fit with the rest: "
            + ", ".join(best.missing or words)
            + ". Correct or drop them.",
            words,
            best.words,
        )
    return None, Finding(
        "no-match", "The text is nowhere in what the game read or wrote.", words
    )


def _in_order(ev: Evidence, o: Occurrence) -> bool:
    """Whether a reader reads the text as a string: its letters one after
    another, a few bytes apart at most — not a dictionary's entries, read
    wherever each lies."""
    addrs = [ev.events[i][1] for i, _ in sorted(o.letters)]
    steps = [b - a for a, b in zip(addrs, addrs[1:], strict=False)]
    return all(0 < d <= STRING_STEP * o.unit for d in steps)


def _chosen(ev: Evidence, text: str, found: list[Occurrence]):
    """The occurrence of several of one kind, or too many places."""
    if _places(ev, found) > TOO_MANY:
        return None, _too_many(text)
    if found[0].kind == "ram":
        found.sort(key=lambda o: (max(o.events), len(o.events)))
    else:
        found.sort(key=lambda o: (max(o.events) - min(o.events), o.events[0]))
    return found[0], None


def _places(ev: Evidence, found: list[Occurrence]) -> int:
    """Distinct addresses the text's first letter is at: one text read or
    written by several routines is one place."""
    return len({ev.events[o.letters[0][0]][1] for o in found if o.letters})


def _too_many(text: str) -> Finding:
    return Finding(
        "too-many",
        "The text fits too many places to tell apart: type more of the line.",
        [w.text for w in ch.words(text)],
    )
