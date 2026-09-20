"""What the encoder looks up in a table, and the atoms it matches against.

The search and the failure report both read a table through the same lookups:
an :class:`Index` per table, built once and kept on it, and the atoms a script
text splits into. Neither of those two modules needs the other, so this one
sits under both.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from mapchar.core.table import (
    BITS,
    RAW,
    RETURN,
    Table,
    TableEntry,
    TableSet,
    TokenKind,
)
from mapchar.core.text import nfd
from mapchar.core.tokens import CodeRef, TextRun, parse_text


@dataclass
class Index:
    """Per-table lookups for encoding, built once per search."""

    by_first: dict[str, list[tuple[tuple, TableEntry]]] = field(default_factory=dict)
    """First atom (a character or ``[label]``) to ``(atoms, entry)`` matches."""
    codes: dict[str, TableEntry] = field(default_factory=dict)
    """Label to CODE entry (operands come from the text)."""
    silent: list[TableEntry] = field(default_factory=list)
    returns: list[TableEntry] = field(default_factory=list)
    longer: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Entry bits to the suffixes that would complete a longer entry."""
    keys: list[str] = field(default_factory=list)
    """Every key, sorted: what a frame falling through over this table looks
    up the keys that would take bits from the table beneath in."""
    min_bits_per_atom: float = 1.0


def atom_key(atom) -> str:
    """How an atom is looked up in :attr:`Index.by_first`."""
    return atom if isinstance(atom, str) else f"[{atom.label}]"


def atoms_equal(a, b) -> bool:
    """Whether two atoms are the same character, or the same code with the
    same operands."""
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return a.label == b.label and a.words == b.words


def index_of(table: Table) -> Index:
    """``table``'s lookups, built once and kept on the table until it changes.

    Every string encoded through a table walks the same entries, and a charset
    table is tens of thousands of them: rebuilding the index per string is most
    of what encoding a block would cost.
    """
    return table.cached("encode_index", lambda: _build_index(table))


def _build_index(table: Table) -> Index:
    idx = Index()
    best = None
    bits_list = list(table.entries)
    for entry in table.entries.values():
        if entry.kind is TokenKind.RETURN:
            idx.returns.append(entry)
            continue
        if entry.kind is TokenKind.CODE:
            idx.codes[entry.text] = entry
            atoms_n = 1
        else:
            try:
                atoms = tuple(atoms_of(entry.text))
            except ValueError:
                continue
            if not atoms:
                if entry.kind is TokenKind.SWITCH:
                    idx.silent.append(entry)
                continue
            idx.by_first.setdefault(atom_key(atoms[0]), []).append((atoms, entry))
            atoms_n = len(atoms)
        ratio = len(entry.bits) / atoms_n
        best = ratio if best is None else min(best, ratio)
    # Aliases: extra text that encodes as an entry's bits without the entry
    # decoding as it (the yen sign on Shift-JIS 5C).
    for text, bits in table.aliases.items():
        entry = table.entries.get(bits)
        if entry is None or entry.kind not in (TokenKind.TEXT, TokenKind.END):
            continue
        atoms = tuple(atoms_of(text))
        if atoms:
            idx.by_first.setdefault(atom_key(atoms[0]), []).append((atoms, entry))
    for lst in idx.by_first.values():
        lst.sort(key=lambda ae: (-len(ae[0]), len(ae[1].bits)))
    # Sorted, every extension of a key follows it without a gap, so the
    # suffixes are found in one pass instead of comparing every pair — a
    # charset table is tens of thousands of entries.
    ordered = sorted(bits_list)
    idx.keys = ordered
    for i, bits in enumerate(ordered):
        suffixes = []
        for other in ordered[i + 1 :]:
            if not other.startswith(bits):
                break
            suffixes.append(other[len(bits) :])
        if suffixes:
            idx.longer[bits] = tuple(suffixes)
    idx.min_bits_per_atom = best if best is not None else 1.0
    return idx


def atoms_of(text: str) -> list[str | CodeRef]:
    """Script ``text`` as the atoms one search step covers: a code, or one
    character of decomposed text.

    Text is decomposed so that a table entry and a translation meet whatever
    form each was typed in: an entry spelling ``が`` matches both of the atoms
    a composed ``が`` makes, and an entry pair of ``か`` and a lone dakuten —
    which is how a ROM that draws the mark separately spells it — matches them
    one at a time.
    """
    atoms: list[str | CodeRef] = []
    for item in parse_text(text):
        if isinstance(item, TextRun):
            atoms.extend(nfd(item.text))
        else:
            atoms.append(item)
    return atoms


def layers_of(stack, tables: TableSet) -> list[tuple[str, tuple[str, ...]]]:
    """The tables the top frame matches in, in the order the decoder tries
    them, each with the tables tried before it: the frame's own, then while a
    frame falls through, the one beneath. A pending return is passed over; a
    ``raw`` or ``bits`` frame beneath matches nothing."""
    layers: list[tuple[str, tuple[str, ...]]] = []
    through = True
    for frame in reversed(stack):
        tid = frame[0]
        if tid.startswith(f"{RETURN}@"):
            continue
        if not through or tid in (RAW, BITS) or tid not in tables.tables:
            break
        layers.append((tid, tuple(t for t, _ in layers)))
        through = frame[5]
    return layers


def extensions(idx: Index, bits: str) -> tuple[str, ...]:
    """The suffixes that would complete one of the table's keys after ``bits``."""
    keys = idx.keys
    out = []
    for i in range(bisect_right(keys, bits), len(keys)):
        if not keys[i].startswith(bits):
            break
        out.append(keys[i][len(bits) :])
    return tuple(out)


def ref_text(ref: CodeRef) -> str:
    """A code atom back as the text spells it, operands included."""
    if ref.words:
        return f"[{ref.label} {' '.join(ref.words)}]"
    return f"[{ref.label}]"
