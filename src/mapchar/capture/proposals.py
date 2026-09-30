"""What the captures propose, stated as the model states it.

Block configurations, table entries, glyphs to label and conflicts, each with
the captures behind it and what is unconfirmed. Offsets here are the ROM
image's; :func:`propose` shifts them by where the image starts in the payload
the project reads. Nothing here changes a project: the UI applies an accepted
proposal through its usual undoable edits. A proposal's id is derived from
what it proposes, so a review mark stays with the same proposal however the
captures behind it are renumbered: a block's from its routine, output, kind
and start, so a capture that extends it keeps the mark; the entries' from
every entry, so a new code makes them a new proposal.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from mapchar.capture.combine import MAX_PARAMS, Combined, Conflict, Engine, Meaning
from mapchar.core.bits import bytes_to_bits
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    NextPointer,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    WriteMode,
)
from mapchar.core.table import OperandSpec, TableEntry, TokenKind

BLOCK = "block"
ENTRIES = "entries"
GLYPH = "glyph"
CONFLICT = "conflict"

END_LABEL = "end"


@dataclass
class Proposal:
    id: str
    kind: str
    title: str
    detail: str = ""
    captures: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    config: BlockConfig | None = None
    """A block's reading."""
    name: str = ""
    """A block's name."""
    entries: list[TableEntry] = field(default_factory=list)
    """Table entries."""
    meaning: Meaning | None = None
    """A glyph to label: the code, and what it draws like."""
    conflict: Conflict | None = None
    offset: int = 0
    """A glyph's or a block's position in the payload, for Confirm in game
    and for showing it."""


def _digest(value) -> str:
    return hashlib.sha1(repr(value).encode()).hexdigest()[:8]


COPY_KINDS = {
    "touched": "read this run",
    "literal": "in reach of an ldr",
    "other": "",
    "data": "no pointer near it: likely graphics or data",
}
"""How each kind of copy (:class:`~mapchar.capture.trace.Copy`) is said."""


def _copies_text(at: int, copies: list, shift: int) -> str:
    """The other words holding a confirmed pointer's value, in the order the
    tracer ranked them: a repoint that rewrites only the confirmed one leaves
    each the game uses pointing at the old string."""
    words = []
    for c in copies:
        how = COPY_KINDS.get(c.kind, "")
        words.append(f"${c.address + shift:X}" + (f" ({how})" if how else ""))
    return (
        f"{len(copies)} other words hold the address the pointer at "
        f"${at + shift:X} holds: " + ", ".join(words)
    )


def _operand(e: Engine, a: int, shift: int) -> str:
    note = e.operands.get(a)
    return f"${a + shift:X}" + (f" ({note})" if note else "")


def _anchor(e: Engine) -> tuple:
    """What a block is known by however far later captures extend it: its
    kind and where it starts — a table's first slot, pointer size and
    mapping; a range's first byte — in the image's offsets."""
    t = e.table
    if t is not None:
        return ("table", t.start, t.size, t.mapping_id)
    return ("range", min(e.starts.values()), e.stream is not None)


def code_bits(m: Meaning) -> str:
    return m.bits or bytes_to_bits(m.code.to_bytes(m.width, "little"))


def _label(m: Meaning) -> str:
    return f"c{m.key}"


def entry_for(m: Meaning, text: str | None = None) -> TableEntry | None:
    """The table entry a meaning is, or None for a glyph with no text yet."""
    bits = code_bits(m)
    text = m.text if text is None else text
    if m.kind == "text" or (m.kind == "glyph" and text):
        return TableEntry(bits, TokenKind.TEXT, text)
    if m.kind == "end":
        return TableEntry(bits, TokenKind.END, f"[{END_LABEL}]")
    if m.kind == "command":
        if 0 < m.params <= MAX_PARAMS:
            spec = {1: "u8", 2: "u16"}.get(m.params, str(m.params))
            return TableEntry(
                bits, TokenKind.CODE, _label(m), operands=(OperandSpec.parse(spec),)
            )
        return TableEntry(bits, TokenKind.TEXT, f"[{_label(m)}]")
    return None


def relabelled(entries: list[TableEntry], table) -> list[TableEntry]:
    """The entries with every code label free in ``table``: a label the
    table already gives other bits gets a number."""
    taken = {label: e.bits for label, e in table.labels.items()}
    out = []
    for e in entries:
        label = e.label
        if label is None or taken.get(label, e.bits) == e.bits:
            if label is not None:
                taken[label] = e.bits
            out.append(e)
            continue
        n = 2
        while f"{label}{n}" in taken:
            n += 1
        new = f"{label}{n}"
        taken[new] = e.bits
        text = (
            new
            if e.kind is TokenKind.CODE
            else e.text.replace(f"[{label}]", f"[{new}]")
        )
        out.append(replace(e, text=text))
    return out


def _string_type(e: Engine):
    if e.end_code is not None:
        return EndToken()
    if e.table is not None:
        return NextPointer()
    if e.fixed_length is not None:
        return FixedLength(e.fixed_length)
    return None


def _source(e: Engine, shift: int):
    if e.table is not None:
        t = e.table
        # Every mapping's target is an offset into the image; the payload
        # holds the image ``shift`` bytes in.
        return PointerTableSource(
            t.start + shift,
            t.stop + shift,
            t.size,
            t.stride,
            t.endian,
            t.mapping_id,
            t.offset + shift,
            t.bank,
            t.null,
        )
    if e.held is not None:
        # Pointers in code: each string through its own, in a list.
        (size, endian, mid, off, bank), slots = e.held
        return PointerListSource(
            tuple(a + shift for a in sorted(slots)),
            size,
            endian,
            mid,
            off + shift,
            bank,
        )
    start = min(e.starts.values())
    stop = max(e.ends.values()) + 1
    if e.fixed_length is not None:
        stop = max(stop, max(e.starts.values()) + e.fixed_length)
    return RangeSource(start + shift, stop + shift)


def _block(n: int, e: Engine, table_id: str, shift: int) -> Proposal:
    caps = sorted(s.capture for s in e.sightings)
    unconfirmed: list[str] = list(e.notes)
    source = _source(e, shift)
    string_type = _string_type(e)
    if string_type is None:
        longest = max(e.ends[c] - e.starts[c] + 1 for c in e.starts)
        string_type = FixedLength(longest)
        unconfirmed.append("the string's end: its length as seen is assumed")
    t = e.table
    if t is not None and not t.confirmed:
        unconfirmed.append(
            "the table's stride is assumed to be its pointer size: another "
            "capture of this engine confirms it"
        )
    if t is not None:
        ext = (t.stop - t.size - t.start) // t.stride + 1
        between = (t.slots[-1] - t.slots[0]) // t.stride + 1 - len(t.slots)
        if ext - len(t.slots) - between > 0:
            unconfirmed.append(
                f"{ext - len(t.slots) - between} slots beyond those seen, read as "
                "the same table because they point near its strings"
            )
        if t.nulls:
            unconfirmed.append(
                f"{len(t.nulls)} slots between those seen hold ${t.null:X}, read "
                "as no string"
            )
        if t.shared:
            unconfirmed.append(
                f"{len(t.shared)} slots between those seen point where another does"
            )
    for lo, hi in e.splits:
        unconfirmed.append(
            f"a split pointer: its low byte at ${lo + shift:X} and its high byte "
            f"at ${hi + shift:X} each move the string"
        )
    for at, copies in sorted(e.copies.items()):
        unconfirmed.append(_copies_text(at, copies, shift))
    if e.loose:
        unconfirmed.append(
            "bytes that move the string with no pointer around them confirmed: "
            + ", ".join(f"${a + shift:X}" for a in sorted(e.loose))
        )
    if e.layout is not None:
        # BlockConfig has no start bit: a packed block starts on a byte.
        order, width, escapes, _starts = e.layout
        unconfirmed.append(
            f"bit-packed: {order.upper()}-first {width}-bit codes, the top "
            f"{escapes} escaping to a second code; the table holds them as bits"
        )
    pointers = ""
    if t is not None:
        pointers = (
            f"pointer table at ${t.start + shift:X}–${t.stop + shift:X}, "
            f"{t.size}-byte {t.mapping_id} pointers every {t.stride} bytes"
        )
    elif e.single:
        held = sorted({a for addrs in e.single.values() for a in addrs})
        if e.held is not None:
            listed = set(e.held[1])
            others = [a for a in held if a not in listed]
            held = [a for a in held if a in listed]
            if others:
                unconfirmed.append(
                    "pointers in code read another way, left out of the list: "
                    + ", ".join(_operand(e, a, shift) for a in others)
                )
            unconfirmed.append(
                "written slotted: its strings lie apart, and packing them would "
                "rewrite whatever lies between them"
            )
        pointers = "pointers in code at " + ", ".join(
            _operand(e, a, shift) for a in held
        )
    elif e.calls:
        calls = ", ".join(f"${a + shift:X}" for a in sorted(set(e.calls.values())))
        pointers = (
            f"no pointer: each string follows a call ({calls}), and the call "
            "site is its reference"
        )
    role = "writer" if e.writer else "reader"
    who = f"{role} {e.reader:X}" if e.reader is not None else f"no single {role}"
    detail = "; ".join(x for x in (who, pointers) if x)
    config = BlockConfig(
        source=source,
        string_type=string_type,
        table_id=table_id,
        # A list of pointers in code reaches strings with other bytes between
        # them: each keeps its own slot.
        write_mode=WriteMode.SLOTTED if isinstance(source, PointerListSource) else None,
    )
    kind = "bitmap" if e.output == "vram" else "RAM"
    reader = f"{e.reader:X}" if e.reader is not None else "none"
    return Proposal(
        f"block:{reader}:{e.output}:{_digest(_anchor(e))}",
        BLOCK,
        f"Block: {len(caps)} capture(s) of {kind} text",
        detail,
        caps,
        unconfirmed,
        config=config,
        name=f"Captured {n + 1}",
        offset=min(e.starts.values()) + shift,
    )


def _entries(c: Combined) -> list[TableEntry]:
    """Every meaning's entry, each end code's label its own: the first
    ``[end]``, the rest ``[end-XX]``."""
    out = []
    ends = 0
    for m in sorted(c.meanings.values(), key=lambda m: code_bits(m)):
        x = entry_for(m)
        if x is None:
            continue
        if m.kind == "end":
            if ends:
                x = replace(x, text=f"[{END_LABEL}-{m.key}]")
            ends += 1
        out.append(x)
    return out


def propose(c: Combined, table_id: str, shift: int = 0) -> list[Proposal]:
    """Every proposal, blocks first. ``shift`` is where the ROM image starts
    in the payload."""
    out: list[Proposal] = []
    for n, e in enumerate(c.engines):
        if e.starts:
            out.append(_block(n, e, table_id, shift))
    kept = _entries(c)
    if kept:
        typed = sum(1 for m in c.meanings.values() if m.typed)
        caps = sorted({cap for m in c.meanings.values() for cap in m.captures})
        unconfirmed = [
            f"{m.key}: {m.note}" for m in c.meanings.values() if not m.confirmed
        ]
        digest = _digest(sorted((x.bits, x.kind.name, x.text) for x in kept))
        out.append(
            Proposal(
                f"entries:{digest}",
                ENTRIES,
                f"Table entries: {len(kept)} codes",
                f"{typed} typed, {len(kept) - typed} from what the engine did "
                "with each code",
                caps,
                unconfirmed,
                entries=kept,
            )
        )
    for m in sorted(c.glyphs, key=lambda m: m.key):
        out.append(
            Proposal(
                f"glyph:{m.key}",
                GLYPH,
                f"Glyph to label: {m.key}",
                m.note,
                sorted(m.captures),
                meaning=m,
            )
        )
    for k in c.conflicts:
        readings = "; ".join(
            f"{r} ({', '.join(sorted(caps))})" for r, caps in k.readings.items()
        )
        out.append(
            Proposal(
                f"conflict:{k.key}",
                CONFLICT,
                f"Conflict: {k.key}",
                readings,
                sorted({c for caps in k.readings.values() for c in caps}),
                conflict=k,
            )
        )
    return out
