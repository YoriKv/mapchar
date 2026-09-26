"""Combining every finished capture.

The readers that recur are the engine. Tables merge, and a code two captures
read differently is a conflict; a bit layout is decided when one fits them
all; an end is an end token the strings agree on, a fixed length when they
start a constant stride apart, or the next pointer; pointer slots found across
captures give a table's stride and base, and the table is extended to strings
never shown while its neighbours still point near the strings seen.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from mapchar.capture import bitlayout
from mapchar.capture.consoles import Console
from mapchar.capture.trace import Result, layout_from_json

LINE_FILL = " "
"""What a line's padding decodes to when a code ends a line."""

TABLE_REACH = 0x4000
"""How far from the strings seen a neighbouring slot's target may land and
still be read as part of the table."""

TABLE_MAX = 1024
"""The most slots a table is extended to."""


@dataclass
class Sighting:
    capture: str
    """The capture's id."""
    typed: str
    result: Result


@dataclass
class Meaning:
    """What one code does, and who says so."""

    code: int
    width: int
    """Bytes the code takes; 0 for a bit code of :attr:`bits`."""
    kind: str
    """``text``, ``end``, ``command`` or ``glyph`` (draws something nobody
    has typed)."""
    text: str = ""
    params: int = 0
    """A command's parameter bytes, when known."""
    bits: str = ""
    """A bit code's bits, first bit first."""
    captures: set[str] = field(default_factory=set)
    typed: bool = False
    """The user typed it; otherwise it was inferred from a change."""
    confirmed: bool = True
    note: str = ""
    image: str = ""
    """Bitmap text: what the code draws, as a digest; codes alike draw alike."""

    @property
    def key(self) -> str:
        return self.bits or f"{self.code:0{2 * self.width}X}"


@dataclass
class Conflict:
    key: str
    readings: dict[str, set[str]]
    """What each reading of the code is — ``kind:text`` — and the captures
    that read it so."""
    code: Meaning
    """The code itself, to make an entry of the reading chosen."""


@dataclass
class PointerTable:
    start: int
    stop: int
    size: int
    stride: int
    endian: str
    mapping_id: str
    offset: int
    bank: int
    slots: list[int]
    """The slots confirmed by a capture."""
    captures: set[str]
    confirmed: bool
    """Whether the stride was seen, not assumed."""


@dataclass
class Engine:
    """The captures read by one routine: one block's worth of strings."""

    reader: int | None
    output: str
    sightings: list[Sighting]
    starts: dict[str, int]
    """Capture to where its string starts."""
    ends: dict[str, int]
    """Capture to its string's last byte, its end token included."""
    unit: int = 1
    end_code: int | None = None
    fixed_length: int | None = None
    table: PointerTable | None = None
    single: dict[str, list[int]] = field(default_factory=dict)
    """Capture to the bytes holding its string's address outside a table."""
    stream: tuple[int, int] | None = None
    layout: tuple | None = None
    """Packed text: ``(order, width, escapes, start bit per capture)``."""
    notes: list[str] = field(default_factory=list)


@dataclass
class Combined:
    engines: list[Engine]
    meanings: dict[str, Meaning]
    conflicts: list[Conflict]
    glyphs: list[Meaning]
    """Codes that draw something nobody has typed yet."""


def _decode(dec: dict[int, str], codes: list[int]) -> str | None:
    if not codes or any(c not in dec for c in codes):
        return None
    return "".join(dec[c] for c in codes)


def _line_end(dec: dict[int, str], out: list[int]) -> str | None:
    """A code that writes a character and then pads the rest of its line."""
    if len(out) < 2 or out[0] not in dec:
        return None
    if len(set(out[1:])) == 1 and dec.get(out[1]) == LINE_FILL:
        return dec[out[0]] + "\\n"
    return None


class _Meanings:
    def __init__(self):
        self.by_key: dict[str, list[Meaning]] = defaultdict(list)

    def add(self, m: Meaning) -> None:
        self.by_key[m.key].append(m)

    def resolve(self) -> tuple[dict[str, Meaning], list[Conflict], list[Meaning]]:
        meanings, conflicts, glyphs = {}, [], []
        for key, ms in self.by_key.items():
            known = [m for m in ms if m.kind != "glyph"]
            if not known:
                g = ms[0]
                for m in ms[1:]:
                    g.captures |= m.captures
                glyphs.append(g)
                continue
            readings: dict[str, set[str]] = defaultdict(set)
            for m in known:
                readings[f"{m.kind}:{m.text}"] |= m.captures
            # A typed reading outweighs an inferred one of the same code.
            typed = {f"{m.kind}:{m.text}" for m in known if m.typed}
            if len(readings) > 1 and len(typed) != 1:
                conflicts.append(Conflict(key, dict(readings), known[0]))
                continue
            pick = next((m for m in known if f"{m.kind}:{m.text}" in typed), known[0])
            for m in known:
                if f"{m.kind}:{m.text}" == f"{pick.kind}:{pick.text}":
                    pick.captures |= m.captures
                    pick.typed |= m.typed
                    pick.confirmed |= m.confirmed
            meanings[key] = pick
        return meanings, conflicts, glyphs


def _ram_meanings(s: Sighting, rom: bytes, out: _Meanings) -> None:
    r, cap = s.result, {s.capture}
    dec = r.decoder
    for ch, code, src in r.typed:
        if src is not None and code in dec:
            out.add(Meaning(rom[src], 1, "text", ch, captures=set(cap), typed=True))
    for c in r.codes:
        if c.kind in ("char", "string"):
            text = _line_end(dec, c.out) or _decode(dec, c.out)
            if text is not None:
                out.add(Meaning(c.value, 1, "text", text, captures=set(cap)))
            else:
                out.add(
                    Meaning(
                        c.value,
                        1,
                        "glyph",
                        captures=set(cap),
                        note="writes " + ".".join(f"{x:02X}" for x in c.out),
                    )
                )
        elif c.kind == "line":
            ch = dec.get(c.out[0]) if c.out else None
            if ch is not None:
                out.add(Meaning(c.value, 1, "text", ch + "\\n", captures=set(cap)))
            else:
                out.add(
                    Meaning(
                        c.value,
                        1,
                        "glyph",
                        captures=set(cap),
                        note=f"writes {c.out[0]:02X} and ends the line",
                    )
                )
        elif c.kind == "empty":
            out.add(
                Meaning(c.value, 1, "command", captures=set(cap), note="writes nothing")
            )
        elif c.kind == "truncate":
            out.add(Meaning(c.value, 1, "end", captures=set(cap)))
        elif c.kind in ("extend", "shift", "rewrite"):
            out.add(
                Meaning(
                    c.value,
                    1,
                    "command",
                    captures=set(cap),
                    confirmed=False,
                    note=f"changes the text around it ({c.kind})",
                )
            )


def _vram_meanings(s: Sighting, out: _Meanings) -> None:
    r, cap, w = s.result, {s.capture}, s.result.unit
    typed_codes = {code: ch for ch, code, _ in r.typed}
    images: dict[str, str] = {}
    for c in r.codes:
        if c.kind == "printable" and c.value in typed_codes:
            images.setdefault(c.image, typed_codes[c.value])
    for code, ch in r.decoder.items():
        out.add(
            Meaning(code, w, "text", ch, captures=set(cap), typed=code in typed_codes)
        )
    for c in r.codes:
        if c.kind == "end":
            out.add(Meaning(c.value, w, "end", captures=set(cap)))
        elif c.kind == "command":
            out.add(
                Meaning(c.value, w, "command", params=max(0, c.skip), captures=set(cap))
            )
        elif c.kind == "printable" and c.value not in r.decoder:
            like = images.get(c.image)
            out.add(
                Meaning(
                    c.value,
                    w,
                    "glyph",
                    captures=set(cap),
                    image=c.image,
                    text=like or "",
                    note=f"draws like {like!r}" if like else "",
                )
            )


def _packed_meanings(engine: Engine, rom: bytes, out: _Meanings) -> None:
    """Packed text: the code table of the one layout every sighting fits."""
    per = [[layout_from_json(d) for d in s.result.layouts] for s in engine.sightings]
    if not all(per):
        engine.notes.append("a sighting fits no bit layout of the model")
        return
    decision = bitlayout.combine(per)
    decided = decision.decided
    if decided is None:
        n = len(decision.keys)
        engine.notes.append(
            "no bit layout fits every sighting"
            if not n
            else f"{n} bit layouts fit: another capture of this text decides"
        )
        return
    (order, width, escapes), starts, table = decided
    engine.layout = (order, width, escapes, starts)
    dec: dict[int, str] = {}
    for s in engine.sightings:
        dec.update(s.result.decoder)
    caps = {s.capture for s in engine.sightings}
    lay = bitlayout.Layout(order, width, escapes, 0, table)
    for code, m in table.items():
        bits = lay.code_bits(code)
        if m == bitlayout.SILENT:
            out.add(
                Meaning(
                    code,
                    0,
                    "command",
                    bits=bits,
                    captures=set(caps),
                    note="prints nothing",
                )
            )
            continue
        a0, n = m
        vals = [a0[1]] if isinstance(a0, tuple) else list(rom[a0 : a0 + n])
        text = _decode(dec, vals)
        if text is None:
            out.add(
                Meaning(
                    code,
                    0,
                    "glyph",
                    bits=bits,
                    captures=set(caps),
                    note="writes " + ".".join(f"{x:02X}" for x in vals),
                )
            )
        else:
            out.add(Meaning(code, 0, "text", text, bits=bits, captures=set(caps)))


def _group(sightings: list[Sighting]) -> list[Engine]:
    groups: dict[tuple, list[Sighting]] = defaultdict(list)
    for s in sightings:
        groups[(s.result.reader, s.result.output, s.result.packed)].append(s)
    engines = []
    for (reader, output, _), ss in groups.items():
        starts, ends = {}, {}
        for s in ss:
            r = s.result
            if r.string is None:
                continue
            first = r.stream[0] if r.stream else r.string[0]
            if r.first_read is not None and r.pointers:
                first = r.first_read
            starts[s.capture] = first
            ends[s.capture] = r.stream[1] if r.stream else r.string[1]
        e = Engine(reader, output, ss, starts, ends, unit=ss[0].result.unit)
        if any(s.result.stream for s in ss):
            e.stream = next(s.result.stream for s in ss if s.result.stream)
        engines.append(e)
    return engines


def _ends(e: Engine, rom: bytes, meanings: dict[str, Meaning]) -> None:
    """End token, fixed length, or the next pointer."""
    agree = set()
    for end in e.ends.values():
        last = int.from_bytes(rom[end - e.unit + 1 : end + 1], "little")
        m = meanings.get(f"{last:0{2 * e.unit}X}")
        agree.add(last if m is not None and m.kind == "end" else None)
    if len(agree) == 1 and None not in agree:
        e.end_code = agree.pop()
        return
    starts = sorted(set(e.starts.values()))
    if len(starts) >= 2 and e.stream is None:  # a packed stream is not a string
        stride = math.gcd(*[b - a for a, b in zip(starts, starts[1:], strict=False)])
        longest = max(e.ends[c] - e.starts[c] + 1 for c in e.starts)
        if stride >= longest:
            e.fixed_length = stride
            return
    if e.table is None:
        e.notes.append("where the strings end is not known: no end token was seen")


def _slots(r: Result, console: Console) -> list[tuple[int, int, str, bool]]:
    """``(slot, size, endian, sure)`` for each pointer a capture confirmed. A
    pointer's bytes move the string by the code unit times a power of 256; a
    slot is *sure* when more than one of its bytes was seen to."""
    moved = {p.address: abs(p.moves) for p in r.pointers}
    out, seen = [], set()
    for a in sorted(moved):
        if a in seen or moved[a] not in (2, 4, 8):
            continue
        unit, size = moved[a], 1
        while moved.get(a + size) == unit * 256**size:
            size += 1
        if size >= 2:
            out.append((a, size, "little", True))
            seen.update(range(a, a + size))
            continue
        down = 1  # big-endian: the byte below moves by more
        while moved.get(a - down) == unit * 256**down:
            down += 1
        if down >= 2:
            out.append((a - down + 1, down, "big", True))
            seen.update(range(a - down + 1, a + 1))
    for a in sorted(moved):
        if a not in seen and moved[a] in (2, 4, 8):
            out.append((a, console.pointer_sizes[0], "little", False))
    return out


def _mapping_for(value: int, target: int, size: int, console: Console, mappings: dict):
    """``(mapping id, offset, bank)`` that turns ``value`` into ``target``,
    the console's own mappings first, a constant added to the value last."""
    for mid in console.mapping_ids:
        m = mappings.get(mid)
        if m is None or size not in getattr(m, "sizes", (size,)):
            continue
        bank_of = getattr(m, "bank_of", None)
        bank = bank_of(target) if bank_of else 0
        try:
            off = m.to_offset(value, bank)
        except Exception:  # noqa: BLE001 - a mapping is a plugin
            continue
        if off is None:
            continue
        if off == target:
            return mid, 0, bank
        if 0 < target - off <= 16:  # a record whose header comes first
            return mid, target - off, bank
    return "linear", target - value, 0


def _tables(e: Engine, rom: bytes, console: Console, mappings: dict) -> None:
    found: dict[tuple, list[tuple[str, int, bool]]] = defaultdict(list)
    for s in e.sightings:
        target = e.starts.get(s.capture)
        if target is None:
            continue
        for slot, size, endian, sure in _slots(s.result, console):
            value = int.from_bytes(rom[slot : slot + size], endian)
            mid, off, bank = _mapping_for(value, target, size, console, mappings)
            found[(size, endian, mid, off, bank)].append((s.capture, slot, sure))
    if not found:
        for s in e.sightings:
            held = [
                p.address for p in s.result.pointers if p.why == "holds the address"
            ]
            if held:
                e.single[s.capture] = held
        return
    # The reading most captures share, then the one seen in most bytes.
    key, hits = max(
        found.items(),
        key=lambda kv: (len({h[0] for h in kv[1]}), sum(h[2] for h in kv[1])),
    )
    size, endian, mid, off, bank = key
    slots = sorted({h[1] for h in hits})
    stride = size
    confirmed = False
    if len(slots) >= 2:
        g = math.gcd(*[b - a for a, b in zip(slots, slots[1:], strict=False)])
        if g >= size:
            stride, confirmed = g, True
    m = mappings.get(mid)
    targets = [e.starts[h[0]] for h in hits]
    lo_t, hi_t = min(targets) - TABLE_REACH, max(targets) + TABLE_REACH
    text = (min(e.starts.values()), max(e.ends.values()))
    seen = set(targets)

    def points_near(slot):
        """A neighbour is read as the table's while it is not the strings'
        own bytes and points near them, somewhere no other slot points."""
        if slot < 0 or slot + size > len(rom) or text[0] - size < slot <= text[1]:
            return False
        v = int.from_bytes(rom[slot : slot + size], endian)
        try:
            t = m.to_offset(v, bank) if m else v
        except Exception:  # noqa: BLE001 - a mapping is a plugin
            return False
        if t is None or not lo_t <= t + off <= hi_t or t + off >= len(rom):
            return False
        if t + off in seen:
            return False
        seen.add(t + off)
        return True

    start, stop = slots[0], slots[-1]
    n = (stop - start) // stride + 1
    while n < TABLE_MAX and points_near(start - stride):
        start -= stride
        n += 1
    while n < TABLE_MAX and points_near(stop + stride):
        stop += stride
        n += 1
    e.table = PointerTable(
        start,
        stop + size,
        size,
        stride,
        endian,
        mid,
        off,
        bank,
        slots,
        {h[0] for h in hits},
        confirmed,
    )


def combine(
    sightings: list[Sighting], rom: bytes, console: Console, mappings: dict
) -> Combined:
    """Everything the finished captures say together."""
    engines = _group([s for s in sightings if s.result.string is not None])
    ms = _Meanings()
    for e in engines:
        for s in e.sightings:
            if s.result.packed:
                continue
            if s.result.output == "ram":
                _ram_meanings(s, rom, ms)
            else:
                _vram_meanings(s, ms)
        if any(s.result.packed for s in e.sightings):
            _packed_meanings(e, rom, ms)
    meanings, conflicts, glyphs = ms.resolve()
    for e in engines:
        _tables(e, rom, console, mappings)
        _ends(e, rom, meanings)
    return Combined(engines, meanings, conflicts, glyphs)
