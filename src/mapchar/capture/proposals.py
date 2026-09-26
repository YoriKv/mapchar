"""What the captures propose, stated as the model states it.

Block configurations, table entries, glyphs to label and conflicts, each with
the captures behind it and what is unconfirmed. Offsets here are the ROM
image's; :func:`propose` shifts them by where the image starts in the payload
the project reads. Nothing here changes a project: the UI applies an accepted
proposal through its usual undoable edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from mapchar.capture.combine import Combined, Conflict, Engine, Meaning
from mapchar.core.bits import bytes_to_bits
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    NextPointer,
    PointerTableSource,
    RangeSource,
)
from mapchar.core.table import OperandSpec, TableEntry, TokenKind

BLOCK = "block"
ENTRIES = "entries"
GLYPH = "glyph"
CONFLICT = "conflict"


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
        return TableEntry(bits, TokenKind.END, "[end]")
    if m.kind == "command":
        if m.params:
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
    if e.fixed_length is not None:
        return FixedLength(e.fixed_length)
    if e.table is not None:
        return NextPointer()
    return None


def _source(e: Engine, shift: int):
    if e.table is not None:
        t = e.table
        return PointerTableSource(
            t.start + shift,
            t.stop + shift,
            t.size,
            t.stride,
            t.endian,
            t.mapping_id,
            t.offset + (shift if t.mapping_id == "linear" else 0),
            t.bank,
        )
    return RangeSource(min(e.starts.values()) + shift, max(e.ends.values()) + 1 + shift)


def _block(n: int, e: Engine, table_id: str, shift: int) -> Proposal:
    caps = sorted(s.capture for s in e.sightings)
    unconfirmed: list[str] = list(e.notes)
    source = _source(e, shift)
    string_type = _string_type(e)
    if string_type is None:
        longest = max(e.ends[c] - e.starts[c] + 1 for c in e.starts)
        string_type = FixedLength(longest)
        unconfirmed.append("the string's end: its length as seen is assumed")
    if e.table is not None and not e.table.confirmed:
        unconfirmed.append(
            "the table's stride is assumed to be its pointer size: another "
            "capture of this engine confirms it"
        )
    if e.table is not None:
        ext = (e.table.stop - e.table.start) // e.table.stride
        if ext > len(e.table.slots):
            unconfirmed.append(
                f"{ext - len(e.table.slots)} slots beyond those seen, read as the "
                "same table because they point near its strings"
            )
    if e.layout is not None:
        order, width, escapes, starts = e.layout
        unconfirmed.append(
            f"bit-packed: {order.upper()}-first {width}-bit codes, the top "
            f"{escapes} escaping to a second code; the table holds them as bits"
        )
    pointers = ""
    if e.table is not None:
        t = e.table
        pointers = (
            f"pointer table at ${t.start + shift:X}–${t.stop + shift:X}, "
            f"{t.size}-byte {t.mapping_id} pointers every {t.stride} bytes"
        )
    elif e.single:
        held = sorted({a for addrs in e.single.values() for a in addrs})
        pointers = "pointers in code at " + ", ".join(f"${a + shift:X}" for a in held)
    reader = f"reader {e.reader:X}" if e.reader is not None else "no single reader"
    detail = "; ".join(x for x in (reader, pointers) if x)
    config = BlockConfig(source=source, string_type=string_type, table_id=table_id)
    kind = "bitmap" if e.output == "vram" else "RAM"
    return Proposal(
        f"block:{n}",
        BLOCK,
        f"Block: {len(caps)} capture(s) of {kind} text",
        detail,
        caps,
        unconfirmed,
        config=config,
        name=f"Captured {n + 1}",
        offset=min(e.starts.values()) + shift,
    )


def propose(c: Combined, table_id: str, shift: int = 0) -> list[Proposal]:
    """Every proposal, blocks first. ``shift`` is where the ROM image starts
    in the payload."""
    out: list[Proposal] = []
    for n, e in enumerate(c.engines):
        if e.starts:
            out.append(_block(n, e, table_id, shift))
    entries = [entry_for(m) for m in c.meanings.values()]
    kept = sorted((x for x in entries if x is not None), key=lambda x: x.bits)
    if kept:
        typed = sum(1 for m in c.meanings.values() if m.typed)
        caps = sorted({cap for m in c.meanings.values() for cap in m.captures})
        unconfirmed = [
            f"{m.key}: {m.note}" for m in c.meanings.values() if not m.confirmed
        ]
        out.append(
            Proposal(
                "entries",
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
