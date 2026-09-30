"""Combining every finished capture.

Captures one routine produced — the writing PC of text written to RAM, the
reading PC of text drawn straight into VRAM — are one engine. Tables merge, and
a code two captures read differently is a conflict; a bit layout is decided
when one fits them all; an end is an end token the strings agree on, the next
pointer when a table reaches them, or a fixed length when the strings start a
constant stride apart that reads as a record size; pointer slots found across
captures give a table's stride and base — pointers found by their value are
operands in code unless evenly spaced — and the table is extended to strings
never shown while its neighbours still point near the strings seen, somewhere
new.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from mapchar.capture import bitlayout
from mapchar.capture.consoles import Console
from mapchar.capture.trace import Result, layout_from_json
from mapchar.plugins.builtins.mappings import parse_banked

LINE_FILL = " "
"""What a line's padding decodes to when a code ends a line."""

LINE_BREAK = "\\n"
"""A line break as a table entry's text spells it."""

TABLE_REACH = 0x4000
"""How far from the strings seen a neighbouring slot's target may land and
still be read as part of the table."""

TABLE_MAX = 1024
"""The most slots a table is extended to."""

PADDED_MAX = 0x100
"""The longest record a string padded to its end is read as, from two
strings: past it, a run of one value is more likely unused space."""

RECORD_MAX = 16
"""The widest record pointers found by their value may be spaced by and still
be read as a table's rather than as operands in code."""

MAX_PARAMS = 8
"""The most parameter bytes a command is read as having; a reader that steps
further has jumped."""

HOLDS = "holds the address"
"""The :attr:`~mapchar.capture.trace.Pointer.why` of a pointer found by its
value, not by a read before the string: an operand in code, often."""


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
    extrapolated: bool = False
    """Read off the alphabet around the letters a chain matched, not matched
    itself: any reading that is not so outweighs it."""

    @property
    def key(self) -> str:
        return self.bits or f"{self.code:0{2 * self.width}X}"

    @property
    def reading(self) -> str:
        """``kind:text``, and a command's parameter bytes: what two readings
        of one code are compared by."""
        out = f"{self.kind}:{self.text}"
        if self.kind == "command" and self.params:
            out += f"({self.params} parameter bytes)"
        return out


@dataclass
class Conflict:
    key: str
    readings: dict[str, set[str]]
    """What each reading of the code is — :attr:`Meaning.reading` — and the
    captures that read it so."""
    code: Meaning
    """The code itself, to make an entry of the reading chosen."""
    choices: dict[str, Meaning] = field(default_factory=dict)
    """Each reading's meaning, to make its entry from: a command's parameter
    bytes differ between readings."""


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
    nulls: list[int] = field(default_factory=list)
    """Slots between those seen that hold :attr:`null`."""
    shared: list[int] = field(default_factory=list)
    """Slots between those seen that point where another slot does."""
    null: int | None = None
    """The raw value that means "no string" (0 or all ones), when a slot
    between those seen holds it."""


@dataclass
class Engine:
    """The captures one routine produced — the routine writing the text to
    RAM, or reading it for VRAM: one block's worth of strings."""

    reader: int | None
    """That routine's PC: the writer for RAM output, the reader for VRAM."""
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
    held: tuple[tuple, list[int]] | None = None
    """A reading — ``(size, endian, mapping id, offset, bank)`` — that pointers
    in code of every capture share, and those pointers' slots: what a list of
    them is read with. None when no one reading reaches every capture's
    string."""
    stream: tuple[int, int] | None = None
    layout: tuple | None = None
    """Packed text: ``(order, width, escapes, start bit per capture)``."""
    notes: list[str] = field(default_factory=list)
    splits: list[tuple[int, int]] = field(default_factory=list)
    """``(low byte, high byte)`` of a pointer whose two bytes are apart:
    each moved the string, but they are not one value."""
    loose: list[int] = field(default_factory=list)
    """Bytes that moved the string by one code unit with no pointer around
    them confirmed."""
    copies: dict[int, list] = field(default_factory=dict)
    """A confirmed GBA pointer's ROM offset to the other words holding its
    value (:class:`~mapchar.capture.trace.Copy`), from every capture."""
    calls: dict[str, int] = field(default_factory=dict)
    """Capture to the ``call`` its string follows, where the call site is the
    string's reference and no pointer holds it (the Game Boy)."""
    operands: dict[int, str] = field(default_factory=dict)
    """A pointer in code's ROM offset to the instruction it is the operand
    of, where the tracer named it."""

    @property
    def writer(self) -> bool:
        """Whether :attr:`reader` is the PC writing the text (RAM output)."""
        return self.output == "ram"


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
        return dec[out[0]] + LINE_BREAK
    return None


def _odd_gaps(r: Result) -> dict[int, str]:
    """Codes standing where the user typed a separator that the decoder gives
    another code: line breaks, most often, where a space was typed."""
    out: dict[int, str] = {}
    for sep, gap in r.gaps:
        if len(sep) != len(gap):
            continue
        for ch, code in zip(sep, gap, strict=True):
            if r.decoder.get(code) != ch:
                out.setdefault(code, ch)
    return out


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
                    if not g.note and m.note:
                        g.note = m.note
                glyphs.append(g)
                continue
            # An alphabet read off around the matched letters gives way to
            # any reading that is not so.
            if any(not m.extrapolated for m in known):
                known = [m for m in known if not m.extrapolated]
            readings: dict[str, set[str]] = defaultdict(set)
            first: dict[str, Meaning] = {}
            for m in known:
                readings[m.reading] |= m.captures
                first.setdefault(m.reading, m)
            pick = _pick(readings, first, known)
            if pick is None:
                conflicts.append(Conflict(key, dict(readings), known[0], first))
                continue
            for m in known:
                pick.captures |= m.captures
                pick.typed |= m.typed
                pick.extrapolated &= m.extrapolated
                if m.reading == pick.reading:
                    pick.confirmed |= m.confirmed
            meanings[key] = pick
        return meanings, conflicts, glyphs


def _pick(readings: dict, first: dict[str, Meaning], known: list[Meaning]):
    """The one reading of a code, or None for a conflict. A typed character
    and a reading of that character followed by more — ``e`` and ``e\\n``,
    the line ending after it — are one reading: the longer, which says what
    the engine does with it. Any other disagreement is a conflict, typed or
    not."""
    if len(readings) == 1:
        return known[0]
    typed = {m.reading for m in known if m.typed}
    if len(typed) != 1:
        return None
    t = first[typed.pop()]
    others = [first[r] for r in readings if r != t.reading]
    if (
        t.kind == "text"
        and len(others) == 1
        and others[0].kind == "text"
        and others[0].text.startswith(t.text)
    ):
        return others[0]
    return None


def _ram_meanings(s: Sighting, rom: bytes, out: _Meanings) -> None:
    r, cap = s.result, {s.capture}
    dec = r.decoder
    odd = _odd_gaps(r)
    # A source's byte is the character's code when the output follows it —
    # copied, or translated through a lookup table (only this output changes)
    # — and not when it only decides the output (a flag, an index).
    tiers = {x.byte: x.tier for x in r.sources}
    for ch, code, src in r.typed:
        if (
            src is not None
            and code in dec
            and tiers.get(src) in ("copied", "only-this")
        ):
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
                        note="writes "
                        + ".".join(f"{x:02X}" for x in c.out)
                        + _gap_note(odd, c.out),
                    )
                )
        elif c.kind == "line":
            ch = dec.get(c.out[0]) if c.out else None
            if ch is not None:
                out.add(Meaning(c.value, 1, "text", ch + LINE_BREAK, captures=set(cap)))
            else:
                what = f"writes {c.out[0]:02X} and ends" if c.out else "ends"
                out.add(
                    Meaning(
                        c.value, 1, "glyph", captures=set(cap), note=f"{what} the line"
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


def _gap_note(odd: dict[int, str], codes: list[int]) -> str:
    hit = next((c for c in codes if c in odd), None)
    if hit is None:
        return ""
    return f"; stands where {odd[hit]!r} was typed: a line break?"


def _vram_meanings(s: Sighting, out: _Meanings) -> None:
    r, cap, w = s.result, {s.capture}, s.result.unit
    odd = _odd_gaps(r)
    typed_codes = {code: ch for ch, code, _ in r.typed}
    images: dict[str, str] = {}
    for c in r.codes:
        if c.kind == "printable" and c.value in typed_codes:
            images.setdefault(c.image, typed_codes[c.value])
    matched = set(typed_codes)
    for sep, gap in r.gaps:
        if len(sep) == len(gap):
            matched |= {c for c in gap if r.decoder.get(c) is not None}
    for code, ch in r.decoder.items():
        out.add(
            Meaning(
                code,
                w,
                "text",
                ch,
                captures=set(cap),
                typed=code in typed_codes,
                extrapolated=code not in matched,
            )
        )
    for c in r.codes:
        if c.kind == "end":
            out.add(Meaning(c.value, w, "end", captures=set(cap)))
        elif c.kind == "command":
            gap = _gap_note(odd, [c.value])
            if 0 <= c.skip <= MAX_PARAMS:
                out.add(
                    Meaning(
                        c.value,
                        w,
                        "command",
                        params=c.skip,
                        captures=set(cap),
                        confirmed=not gap,
                        note=gap[2:],
                    )
                )
            else:
                out.add(
                    Meaning(
                        c.value,
                        w,
                        "command",
                        captures=set(cap),
                        confirmed=False,
                        note=f"the reader jumps {c.skip:+d} bytes from it{gap}",
                    )
                )
        elif c.kind == "printable" and c.value not in r.decoder:
            like = images.get(c.image)
            note = f"draws like {like!r}" if like else ""
            gap = _gap_note(odd, [c.value])
            out.add(
                Meaning(
                    c.value,
                    w,
                    "glyph",
                    captures=set(cap),
                    image=c.image,
                    text=like or "",
                    note=(note + gap) if note else gap[2:],
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


def _in_stream(src) -> bool:
    """Whether a source is the string's own byte, not a dictionary's: the
    tracer marks the others once it keeps them."""
    return getattr(src, "stream", True) is not False


def _bounds(r: Result) -> tuple[int, int]:
    """Where a result's string starts and its last byte. Sources the tracer
    marks as not the string's own — a dictionary's — bound nothing."""
    first = r.stream[0] if r.stream else r.string[0]
    last = r.stream[1] if r.stream else r.string[1]
    if not r.stream:
        own = [x.byte for x in r.sources if _in_stream(x)]
        other = {x.byte for x in r.sources if not _in_stream(x)}
        if own and other:
            if first in other:
                first = min(own)
            if last in other:
                last = max(own)
    if r.first_read is not None and r.pointers:
        first = r.first_read
    return first, last


def _group(sightings: list[Sighting]) -> list[Engine]:
    groups: dict[tuple, list[Sighting]] = defaultdict(list)
    for s in sightings:
        groups[(s.result.reader, s.result.output, s.result.packed)].append(s)
    engines = []
    for (reader, output, _), ss in groups.items():
        starts, ends = {}, {}
        for s in ss:
            if s.result.string is None:
                continue
            starts[s.capture], ends[s.capture] = _bounds(s.result)
        units = Counter(s.result.unit for s in ss)
        unit = units.most_common(1)[0][0]
        e = Engine(reader, output, ss, starts, ends, unit=unit)
        for s in ss:
            r = s.result
            for c in getattr(r, "copies", []):
                have = e.copies.setdefault(c.of, [])
                if all(x.address != c.address for x in have):
                    have.append(c)
            if getattr(r, "follows_call", None) is not None:
                e.calls[s.capture] = r.follows_call
            for p in r.pointers:
                if getattr(p, "note", ""):
                    e.operands[p.address] = p.note
        if len(units) > 1:
            e.notes.append(
                "the captures read codes of different widths ("
                + ", ".join(f"{u} bytes" for u in sorted(units))
                + f"): {unit} is assumed"
            )
        if any(s.result.stream for s in ss):
            e.stream = next(s.result.stream for s in ss if s.result.stream)
        engines.append(e)
    return engines


def _ends(e: Engine, rom: bytes, meanings: dict[str, Meaning]) -> None:
    """End token, the next pointer, or a fixed length."""
    agree = set()
    for end in e.ends.values():
        last = int.from_bytes(rom[end - e.unit + 1 : end + 1], "little")
        m = meanings.get(f"{last:0{2 * e.unit}X}")
        agree.add(last if m is not None and m.kind == "end" else None)
    if len(agree) == 1 and None not in agree:
        e.end_code = agree.pop()
        return
    if e.table is not None:
        return  # each string runs to where the next pointer lands
    stride = _record_size(e, rom)
    if stride is not None:
        e.fixed_length = stride
        e.notes.append(
            f"a fixed length of {stride}: {len(set(e.starts.values()))} strings "
            "start that far apart, and no end token was seen"
        )
        return
    e.notes.append("where the strings end is not known: no end token was seen")


def _record_size(e: Engine, rom: bytes) -> int | None:
    """The stride the strings start apart, when it reads as the size of a
    record holding each: the bytes past each string to the next record all
    one padding value (a record of :data:`PADDED_MAX` bytes at most), or —
    with three strings or more — under twice the longest string."""
    starts = sorted(set(e.starts.values()))
    if len(starts) < 2 or e.stream is not None:  # a packed stream is not a string
        return None
    stride = math.gcd(*[b - a for a, b in zip(starts, starts[1:], strict=False)])
    longest = max(e.ends[c] - e.starts[c] + 1 for c in e.starts)
    if stride < longest:
        return None
    if len(starts) >= 3 and stride < 2 * longest:
        return stride
    if stride > PADDED_MAX:
        return None
    pad = set()
    for c, start in e.starts.items():
        pad |= set(rom[e.ends[c] + 1 : start + stride])
    return stride if len(pad) == 1 else None


@dataclass
class _Slot:
    at: int
    size: int
    endian: str
    sure: bool
    why: str


def _slots(r: Result, console: Console) -> tuple[list[_Slot], list[tuple[int, int]]]:
    """The pointer slots a capture confirmed, and its split pointers. A
    pointer's bytes move the string by the code unit times a power of 256; a
    slot is *sure* when more than one of its bytes was seen to. A byte that
    moved it by the unit with the byte moving it 256 times as far elsewhere is
    a split pointer, not a slot."""
    moved = {p.address: abs(p.moves) for p in r.pointers}
    why = {p.address: p.why for p in r.pointers}
    out: list[_Slot] = []
    seen: set[int] = set()
    for a in sorted(moved):
        if a in seen or moved[a] not in (2, 4, 8):
            continue
        unit, size = moved[a], 1
        while moved.get(a + size) == unit * 256**size:
            size += 1
        if size >= 2:
            out.append(_Slot(a, size, "little", True, why[a]))
            seen.update(range(a, a + size))
            continue
        down = 1  # big-endian: the byte below moves by more
        while moved.get(a - down) == unit * 256**down:
            down += 1
        if down >= 2:
            out.append(_Slot(a - down + 1, down, "big", True, why[a]))
            seen.update(range(a - down + 1, a + 1))
    splits = []
    for a in sorted(moved):
        if a in seen or moved[a] not in (2, 4, 8):
            continue
        hi = next(
            (
                b
                for b in sorted(moved)
                if b not in seen and abs(b - a) > 1 and moved[b] == moved[a] * 256
            ),
            None,
        )
        if hi is not None:
            splits.append((a, hi))
            seen.update((a, hi))
            continue
        out.append(_Slot(a, console.pointer_sizes[0], "little", False, why[a]))
    return out, splits


def _lookup(mappings: dict, mid: str):
    """The mapping of an id: from ``mappings``, or built as the registry
    builds a parameterised ``banked:<base>:<size>`` id, which a listing of
    the registry's ids does not hold."""
    m = mappings.get(mid)
    return m if m is not None else parse_banked(mid)


def _mappings(console: Console, mappings: dict, size: int):
    for mid in console.mapping_ids:
        m = _lookup(mappings, mid)
        if m is not None and size in getattr(m, "sizes", (size,)):
            yield mid, m


def _bank(m, target: int) -> int:
    bank_of = getattr(m, "bank_of", None)
    return bank_of(target) if bank_of else 0


def _to_offset(m, value: int, bank: int) -> int | None:
    try:
        return m.to_offset(value, bank)
    except Exception:  # noqa: BLE001 - a mapping is a plugin
        return None


def _mapping_for(value: int, target: int, size: int, console: Console, mappings: dict):
    """``(mapping id, offset, bank)`` that turns ``value`` into ``target``,
    the console's own mappings first, a constant added to the value last."""
    for mid, m in _mappings(console, mappings, size):
        bank = _bank(m, target)
        off = _to_offset(m, value, bank)
        if off is None:
            continue
        if off == target:
            return mid, 0, bank
        if 0 < target - off <= 16:  # a record whose header comes first
            return mid, target - off, bank
    return "linear", target - value, 0


def _widen(
    slot: _Slot, rom: bytes, target: int, console: Console, mappings: dict
) -> _Slot | None:
    """A sure slot read wider, when the console's pointers are and a console
    mapping reads the wider value as the string's address exactly whatever
    bank it is told — the value carries its own — or None. The tracer moves
    a pointer's two low bytes, so a bank byte above them is seen only so; a
    zero above them is no bank byte, but the next field."""
    for wide in sorted(s for s in console.pointer_sizes if s > slot.size):
        at = slot.at if slot.endian == "little" else slot.at - (wide - slot.size)
        if at < 0 or at + wide > len(rom):
            continue
        extra = (
            rom[at + slot.size : at + wide]
            if slot.endian == "little"
            else rom[at : at + wide - slot.size]
        )
        if not any(extra):
            continue
        value = int.from_bytes(rom[at : at + wide], slot.endian)
        for _mid, m in _mappings(console, mappings, wide):
            if _to_offset(m, value, 0) == _to_offset(m, value, 0x5A) == target:
                return _Slot(at, wide, slot.endian, True, slot.why)
    return None


@dataclass
class _Hit:
    capture: str
    slot: int
    sure: bool
    why: str


def _key(sl: _Slot, rom: bytes, target: int, console: Console, mappings: dict):
    value = int.from_bytes(rom[sl.at : sl.at + sl.size], sl.endian)
    mid, off, bank = _mapping_for(value, target, sl.size, console, mappings)
    return sl.size, sl.endian, mid, off, bank


def _operand_table(held: list[_Hit], size: int) -> bool:
    """Whether pointers found by their value are a table's, not operands in
    code: three or more evenly spaced, a pointer or a small record apart.
    Operands sit wherever the code puts them."""
    slots = sorted({h.slot for h in held})
    if len(slots) < 3:
        return False
    steps = {b - a for a, b in zip(slots, slots[1:], strict=False)}
    return len(steps) == 1 and size <= steps.pop() <= RECORD_MAX


def _tables(e: Engine, rom: bytes, console: Console, mappings: dict) -> None:
    # Each reading's slots; a reading is read wider only when every sure
    # slot of it is.
    narrow: dict[tuple, list[tuple[_Hit, _Slot | None, int]]] = defaultdict(list)
    for s in e.sightings:
        target = e.starts.get(s.capture)
        if target is None:
            continue
        slots, splits = _slots(s.result, console)
        e.splits += [x for x in splits if x not in e.splits]
        for sl in slots:
            wide = _widen(sl, rom, target, console, mappings) if sl.sure else None
            hit = _Hit(s.capture, sl.at, sl.sure, sl.why)
            narrow[_key(sl, rom, target, console, mappings)].append((hit, wide, target))
    found: dict[tuple, list[_Hit]] = defaultdict(list)
    for key, entries in narrow.items():
        sure = [w for h, w, _ in entries if h.sure]
        widen = bool(sure) and None not in sure and len({w.size for w in sure}) == 1
        for h, w, t in entries:
            if widen and w is not None:
                wkey = _key(w, rom, t, console, mappings)
                found[wkey].append(_Hit(h.capture, w.at, True, h.why))
            else:
                found[key].append(h)
    # A table is sure slots, or one reading several captures share. Pointers
    # found by their value are operands in code unless evenly spaced.
    tables = {}
    held_by: dict[tuple, dict[str, list[int]]] = {}
    for key, hits in sorted(found.items()):
        held = [h for h in hits if h.why == HOLDS]
        read = [h for h in hits if h.why != HOLDS]
        if held and _operand_table(held, key[0]):
            read, held = hits, []
        for h in held:
            got = e.single.setdefault(h.capture, [])
            if h.slot not in got:
                got.append(h.slot)
            slots = held_by.setdefault(key, {}).setdefault(h.capture, [])
            if h.slot not in slots:
                slots.append(h.slot)
        if not read:
            continue
        if any(h.sure for h in read) or len({h.capture for h in read}) >= 2:
            tables[key] = read
            continue
        e.loose += [h.slot for h in read if h.slot not in e.loose]
    # A list of them is read with one reading: only when one reads every
    # capture's string, else the strings are proposed as the range they span.
    every = set(e.starts)
    whole = [(k, caps) for k, caps in sorted(held_by.items()) if every <= set(caps)]
    if whole:
        key, caps = max(whole, key=lambda kc: sum(map(len, kc[1].values())))
        e.held = (key, sorted({a for addrs in caps.values() for a in addrs}))
    if not tables:
        if not e.single:
            for s in e.sightings:
                held = [p.address for p in s.result.pointers if p.why == HOLDS]
                if held:
                    e.single[s.capture] = held
        return
    # The reading most captures share, then the one seen in most bytes, then
    # the lowest slot: the first of those in key order.
    key, hits = max(
        sorted(tables.items()),
        key=lambda kv: (
            len({h.capture for h in kv[1]}),
            sum(h.sure for h in kv[1]),
            -min(h.slot for h in kv[1]),
        ),
    )
    e.table = _table(e, rom, key, hits, mappings)


def _one_slot_each(hits: list[_Hit]) -> dict[str, int]:
    """Each capture's slot in a table: of several, the one nearest another
    capture's, since a string reached from two tables is in one of them."""
    by_cap: dict[str, list[int]] = defaultdict(list)
    for h in hits:
        by_cap[h.capture].append(h.slot)
    out = {}
    for cap, slots in by_cap.items():
        others = [x for c, xs in by_cap.items() if c != cap for x in xs]
        if others:
            out[cap] = min(slots, key=lambda x: (min(abs(x - o) for o in others), x))
        else:
            sure = [h.slot for h in hits if h.capture == cap and h.sure]
            out[cap] = min(sure or slots)
    return out


def _table(e: Engine, rom: bytes, key: tuple, hits: list[_Hit], mappings: dict):
    size, endian, mid, off, bank = key
    chosen = _one_slot_each(hits)
    slots = sorted(set(chosen.values()))
    stride = size
    confirmed = False
    if len(slots) >= 2:
        g = math.gcd(*[b - a for a, b in zip(slots, slots[1:], strict=False)])
        if g >= size:
            stride, confirmed = g, True
    m = _lookup(mappings, mid)
    targets = [e.starts[c] for c in chosen]
    lo_t, hi_t = min(targets) - TABLE_REACH, max(targets) + TABLE_REACH
    spans = [(e.starts[c], e.ends[c]) for c in e.starts]
    nothing = (0, (1 << 8 * size) - 1)
    seen = set(targets)

    def raw(slot: int) -> int:
        return int.from_bytes(rom[slot : slot + size], endian)

    def target(slot: int) -> int | None:
        t = _to_offset(m, raw(slot), bank) if m else raw(slot)
        return None if t is None else t + off

    def fresh(slot: int) -> int | None:
        """The target of a neighbour read as the table's: pointing near the
        strings seen, somewhere no other slot points. A string's own bytes
        are never a slot, and a null ends the table."""
        if slot < 0 or slot + size > len(rom):
            return None
        if any(lo - size < slot <= hi for lo, hi in spans):
            return None
        if raw(slot) in nothing:
            return None
        t = target(slot)
        if t is None or not lo_t <= t <= hi_t or t >= len(rom) or t in seen:
            return None
        return t

    # Between the slots seen: a null holds no string, and a slot may share
    # another's.
    start, stop = slots[0], slots[-1]
    nulls: list[int] = []
    shared: list[int] = []
    null: int | None = None
    for at in range(start + stride, stop, stride):
        if at in slots:
            continue
        v = raw(at)
        if v in nothing:
            null = v if null is None else null
            if v == null:
                nulls.append(at)
            continue
        t = target(at)
        if t is not None:
            if t in seen:
                shared.append(at)
            seen.add(t)
    # Beyond them, only slots pointing somewhere new.
    n = (stop - start) // stride + 1
    while n < TABLE_MAX and (t := fresh(start - stride)) is not None:
        start -= stride
        seen.add(t)
        n += 1
    while n < TABLE_MAX and (t := fresh(stop + stride)) is not None:
        stop += stride
        seen.add(t)
        n += 1
    return PointerTable(
        start,
        stop + size,
        size,
        stride,
        endian,
        mid,
        off,
        bank,
        slots,
        set(chosen),
        confirmed,
        nulls,
        shared,
        null,
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
