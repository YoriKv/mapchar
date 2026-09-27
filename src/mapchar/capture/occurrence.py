"""Finding the typed text in a replay's evidence.

The text is looked for with :mod:`mapchar.capture.chains` in two kinds of
place: the RAM writes — per writing PC, and the final contents of each run of
addresses — and the ROM reads per reading PC. Of the places that hold all of
it, the one holding it earliest is nearest the ROM and is the occurrence; RAM
writes come before ROM reads, which are for text drawn straight into VRAM.

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
    (``ram``), or the matched letters' reads (``rom``)."""
    letters: list[tuple[int, str]]
    """``(evidence index, typed character)`` per matched letter."""
    order: dict[str, str]
    decoder: dict[int, str]
    """Code to character, from the typed text."""
    gaps: list[tuple[str, list[int]]] = field(default_factory=list)
    """Typed separator and the codes between those two words."""


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


def _letters(text: str) -> int:
    return sum(
        len(w) for w in ch.words(text, ch.JIS if ch.has_kana(text) else ch.GOJUON)
    )


def check_text(text: str) -> Finding | None:
    """What makes typed text unsearchable before any search."""
    ws = ch.words(text, ch.JIS if ch.has_kana(text) else ch.GOJUON)
    if not ws:
        return Finding(
            "no-letters",
            "Type the text with kana or Latin letters: nothing else can be "
            "searched for.",
        )
    if _letters(text) < MIN_LETTERS:
        return Finding(
            "too-short", "Type more of the line: so little text fits too many places."
        )
    return None


class _Best:
    """The partial chain holding the most words, for the no-match report."""

    def __init__(self):
        self.count = 0
        self.words: list[str] = []

    def see(self, found: list[ch.Chain], ws: list[ch.Word]) -> None:
        for c in found:
            if c.count > self.count:
                self.count = c.count
                self.words = [ws[k].text for _, k in c.placed]


def _ram(ev: Evidence, text: str, order, best: _Best) -> Step[list]:
    evs = ev.events
    writes = [i for i, e in enumerate(evs) if e[0] == "W"]
    by_pc: dict[int, list[int]] = defaultdict(list)
    for i in writes:
        by_pc[evs[i][4]].append(i)
    final = {evs[i][1]: i for i in writes}  # each address's last write
    ws = ch.words(text, order)
    out = []
    for pc, idx in by_pc.items():
        seq = [evs[i][2] for i in idx]
        found = ch.match(seq, ws)
        best.see(found, ws)
        for c in ch.complete(found, ws):
            out.append(
                _ram_occ(
                    ev,
                    idx[c.start : c.end],
                    c,
                    ws,
                    order,
                    f"writes by {pc:X}",
                    pc,
                    c.start,
                )
            )
        yield
    run: list[int] = []
    for a in sorted(final) + [None]:
        if run and (a is None or a != run[-1] + 1):
            if len(run) >= RUN_MIN:
                seq = [evs[final[x]][2] for x in run]
                found = ch.match(seq, ws)
                best.see(found, ws)
                for c in ch.complete(found, ws):
                    idx = [final[x] for x in run[c.start : c.end]]
                    out.append(
                        _ram_occ(
                            ev,
                            sorted(idx),
                            c,
                            ws,
                            order,
                            "final contents",
                            None,
                            c.start,
                            idx,
                        )
                    )
            run = []
        if a is not None:
            run.append(a)
    yield
    return out


def _ram_occ(ev, idx, c, ws, order, how, pc, base, positional=None):
    """An occurrence among RAM writes: ``idx`` the writes it spans in log
    order; ``positional`` the same writes in chain order when that differs."""
    pos = positional if positional is not None else idx
    full = [ev.events[i][2] for i in pos]
    shifted = ch.Chain(
        0, c.end - base, c.bases, tuple((p - base, k) for p, k in c.placed)
    )
    letters = [(pos[p], chr_) for p, chr_ in ch.letters_at(shifted, ws)]
    return Occurrence(
        "ram",
        how,
        pc,
        idx,
        letters,
        order,
        ch.decoder(full, shifted, ws, order),
        ch.gaps(full, shifted, ws),
    )


def _rom(ev: Evidence, text: str, order, best: _Best) -> Step[list]:
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
        seq = [evs[i][2] for i in s]
        found = ch.match(seq, ws)
        best.see(found, ws)
        for c in ch.complete(found, ws):
            letters = [(s[p], chr_) for p, chr_ in ch.letters_at(c, ws)]
            out.append(
                Occurrence(
                    "rom",
                    f"reads by {pc:X}",
                    pc,
                    sorted(i for i, _ in letters),
                    sorted(letters),
                    order,
                    ch.decoder(seq, c, ws, order),
                    ch.gaps(seq, c, ws),
                )
            )
        yield
    return out


def _in_ram_now(ev: Evidence, text: str, order) -> str | None:
    """Where the text sits in RAM at the capture point, 1 or 2 bytes a code."""
    ws = ch.words(text, order)
    for name in ev.console.rams[:-1]:
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
    ram: list[Occurrence] = []
    for order in orders:
        ram += yield from _ram(ev, text, order, best)
    if ram:
        ram.sort(key=lambda o: (max(o.events), len(o.events)))
        if _places(ev, ram) > TOO_MANY:
            return None, _too_many(text)
        return ram[0], None
    rom: list[Occurrence] = []
    for order in orders:
        rom += yield from _rom(ev, text, order, best)
    if rom:
        if _places(ev, rom) > TOO_MANY:
            return None, _too_many(text)
        rom.sort(key=lambda o: (max(o.events) - min(o.events), o.events[0]))
        return rom[0], None
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
        missing = [w for w in words if w not in best.words]
        return None, Finding(
            "no-match",
            "These words do not fit with the rest: "
            + ", ".join(missing or words)
            + ". Correct or drop them.",
            words,
            best.words,
        )
    return None, Finding(
        "no-match", "The text is nowhere in what the game read or wrote.", words
    )


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
