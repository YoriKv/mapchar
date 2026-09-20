"""The sentence an encode that failed ends with.

Prose over the values the search already has — the atom it got furthest to,
the frames in use there, the tables' indexes — so that a translator is told
which table holds the character, what switches into it, or which operand does
not fit, rather than that no encoding was found. :func:`why` is all
:mod:`mapchar.engines.encode` calls.
"""

from __future__ import annotations

from mapchar.core.table import TableSet, TokenKind
from mapchar.core.text import is_mark, nfc
from mapchar.core.tokens import CodeRef, bits_for, operand_values
from mapchar.engines.encode_index import (
    Index,
    atom_key,
    atoms_equal,
    layers_of,
    ref_text,
)


def why(atoms, at: int, stack, tables: TableSet, indexes) -> str:
    """Why the search found no encoding, in the terms the text is written in.

    The search itself only knows that it ran out of states. The atom it got
    furthest to, and the tables in use when it did, are what tell a character
    no table has an entry for from one whose table nothing switches to there,
    and both from one that simply cannot follow what comes before it.
    """
    if at >= len(atoms):
        if len(stack) > 1:
            return "the text encodes, but a table it switches into is never left"
        return (
            "the text encodes, but it cannot end there: its last entry begins "
            "a longer one, whose bits would be read instead"
        )
    here = [tid for tid, _ in layers_of(stack, tables)]
    atom = atoms[at]
    if isinstance(atom, CodeRef):
        return _why_code(atom, here, tables, indexes)
    return _why_char(atoms, at, here, tables, indexes)


def _why_char(atoms, at: int, here: list[str], tables: TableSet, indexes) -> str:
    key = atom_key(atoms[at])
    shown = _shown_char(atoms, at)
    starting = [tid for tid, idx in indexes.items() if key in idx.by_first]
    if not starting:
        return f"no table has an entry for {shown}"
    matching = [tid for tid in starting if _entry_matches(indexes[tid], key, atoms, at)]
    if not matching:
        return (
            f"no table has an entry for {shown} on its own; it is only ever "
            f"part of a longer entry"
        )
    return _why_reachable(shown, matching, here, tables)


def _why_code(ref: CodeRef, here: list[str], tables: TableSet, indexes) -> str:
    shown = ref_text(ref)
    if ref.is_raw_byte or ref.is_raw_bits:
        return (
            f"{shown} cannot stand there: a table in use would read those bits "
            f"as an entry of its own"
        )
    coded = [tid for tid, idx in indexes.items() if ref.label in idx.codes]
    plain = [tid for tid, idx in indexes.items() if f"[{ref.label}]" in idx.by_first]
    if not coded and not plain:
        return f"no table has a code [{ref.label}]"
    if coded:
        operands = _why_operands(ref, coded, indexes)
        if operands is not None:
            return operands
    if plain and not coded and ref.words:
        return f"[{ref.label}] takes no operands"
    return _why_reachable(shown, coded + plain, here, tables)


def _why_operands(ref: CodeRef, coded: list[str], indexes) -> str | None:
    """Why no table can write ``ref``'s operands, or ``None`` when one can."""
    reason = None
    for tid in coded:
        entry = indexes[tid].codes[ref.label]
        specs = " ".join(spec.spec() for spec in entry.operands)
        try:
            values = operand_values(entry, ref.words)
            bits_for(entry, values)
        except ValueError as exc:
            # operand_values names the code itself; a word that will not parse
            # raises the word alone, which says nothing on its own.
            said = str(exc)
            reason = reason or (
                said
                if said.startswith("[")
                else f"[{ref.label}] takes operands {specs}, not {said!r}"
            )
        except OverflowError:
            reason = reason or (
                f"[{ref.label}] takes operands {specs}, which "
                f"{' '.join(ref.words)} does not fit"
            )
        else:
            return None
    return reason


def _why_reachable(
    shown: str, holders: list[str], here: list[str], tables: TableSet
) -> str:
    """Why an entry that exists cannot be used where the text wants it."""
    if not here:
        return f"{shown} cannot go there: those bits are read raw, not in a table"
    in_use = [tid for tid in holders if tid in here]
    if not in_use:
        switch = _switch_into(holders, here, tables)
        how = (
            f", so write [{switch}] first"
            if switch
            else ", and nothing switches to it there"
        )
        return (
            f"{shown} is in {_named(holders)}; the text is read in "
            f"{_named(here)} there{how}"
        )
    return (
        f"{shown} has an entry in {_named(in_use)}, but it cannot follow the "
        f"text before it"
    )


def _switch_into(holders: list[str], here: list[str], tables: TableSet) -> str | None:
    """The label of a switch out of a table in use that reaches one of
    ``holders``: what the translator writes to get there."""
    for tid in here:
        for entry in tables.table(tid).entries.values():
            if entry.kind is not TokenKind.SWITCH or entry.label is None:
                continue
            if any(p.table_id in holders for p in entry.params):
                return entry.label
    return None


def _entry_matches(idx: Index, key: str, atoms, at: int) -> bool:
    """Whether an entry beginning with ``key`` matches the atoms from ``at``."""
    n = len(atoms)
    return any(
        at + len(entry_atoms) <= n
        and all(
            atoms_equal(entry_atoms[i], atoms[at + i]) for i in range(len(entry_atoms))
        )
        for entry_atoms, _ in idx.by_first.get(key, ())
    )


def _shown_char(atoms, at: int) -> str:
    """The character at ``at`` as the text spells it, with its code points.

    A combining mark is shown on the character it joins: a table with no entry
    for ``é`` fails on the acute, and naming the acute alone would send the
    translator looking for a character they never typed.
    """
    text = atoms[at]
    if is_mark(text) and at and isinstance(atoms[at - 1], str):
        text = nfc(atoms[at - 1] + text)
    points = " ".join(f"U+{ord(c):04X}" for c in text)
    return f"{text!r} ({points})"


def _named(ids: list[str]) -> str:
    shown = [f"@{tid}" for tid in ids[:3]]
    if len(ids) > 3:
        shown.append("…")
    return ("table " if len(ids) == 1 else "tables ") + ", ".join(shown)
