"""The encode engine: script text to bits through a table set.

A Dijkstra search over ``(position, frame stack, forbidden suffixes)`` for
the cheapest bit string that the decode engine turns back into the same
tokens. The forbidden suffixes are what keep longest-prefix decoding honest:
after an entry that is a proper prefix of a longer entry, the bits that would
complete the longer entry may not follow. Every result is verified by
decoding it.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from itertools import count

from mapchar.core.bits import Bits, bits_to_bytes
from mapchar.core.errors import EncodeError
from mapchar.core.table import (
    BITS,
    RAW,
    RETURN,
    Entry,
    EntryKind,
    SwitchParam,
    Table,
    TableSet,
)
from mapchar.core.tokens import (
    CodeRef,
    TextRun,
    bits_for,
    operand_values,
    parse_text,
    render,
)
from mapchar.engines.decode import DecodeRules, EndedBy, decode_run, innermost_index

_Frame = tuple[str, int | None, bool, str | None]
"""``(table_id, counter, shared, fallback_bits)``; counter None is unlimited."""


@dataclass
class EncodeResult:
    bits: str
    ends_with_end: bool
    """The last token is an end token."""

    @property
    def data(self) -> bytes:
        return bits_to_bytes(self.bits)


@dataclass
class _Index:
    """Per-table lookups for encoding, built once per search."""

    by_first: dict[str, list[tuple[tuple, Entry]]] = field(default_factory=dict)
    """First atom (a character or ``[label]``) to ``(atoms, entry)`` matches."""
    codes: dict[str, Entry] = field(default_factory=dict)
    """Label to CODE entry (operands come from the text)."""
    silent: list[Entry] = field(default_factory=list)
    returns: list[Entry] = field(default_factory=list)
    longer: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Entry bits to the suffixes that would complete a longer entry."""
    min_bits_per_atom: float = 1.0


def _atom_key(atom) -> str:
    return atom if isinstance(atom, str) else f"[{atom.label}]"


def _atoms_equal(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return a.label == b.label and a.words == b.words


def _index(table: Table) -> _Index:
    idx = _Index()
    best = None
    bits_list = list(table.entries)
    for entry in table.entries.values():
        if entry.kind is EntryKind.RETURN:
            idx.returns.append(entry)
            continue
        if entry.kind is EntryKind.CODE:
            idx.codes[entry.text] = entry
            atoms_n = 1
        else:
            try:
                atoms = tuple(_atoms(entry.text))
            except ValueError:
                continue
            if not atoms:
                if entry.kind is EntryKind.SWITCH:
                    idx.silent.append(entry)
                continue
            idx.by_first.setdefault(_atom_key(atoms[0]), []).append((atoms, entry))
            atoms_n = len(atoms)
        ratio = len(entry.bits) / atoms_n
        best = ratio if best is None else min(best, ratio)
    for lst in idx.by_first.values():
        lst.sort(key=lambda ae: (-len(ae[0]), len(ae[1].bits)))
    for bits in bits_list:
        suffixes = tuple(
            other[len(bits) :]
            for other in bits_list
            if len(other) > len(bits) and other.startswith(bits)
        )
        if suffixes:
            idx.longer[bits] = suffixes
    idx.min_bits_per_atom = best if best is not None else 1.0
    return idx


def _atoms(text: str) -> list[str | CodeRef]:
    atoms: list[str | CodeRef] = []
    for item in parse_text(text):
        if isinstance(item, TextRun):
            atoms.extend(item.text)
        else:
            atoms.append(item)
    return atoms


def _forbid(forbidden: tuple[str, ...], emitted: str) -> tuple[str, ...] | None:
    """Advance the forbidden suffixes past ``emitted``; None on a violation."""
    if not emitted:
        return forbidden
    out = []
    for suffix in forbidden:
        if suffix.startswith(emitted):
            rest = suffix[len(emitted) :]
            if not rest:
                return None
            out.append(rest)
        elif emitted.startswith(suffix):
            return None
    return tuple(out)


def _count(stack: list[_Frame], weight: int) -> list[_Frame]:
    """Charge a match to the top frame (and below while shared); pop used-up frames."""
    stack = list(stack)
    i = len(stack) - 1
    while i >= 0:
        tid, counter, shared, fb = stack[i]
        if counter is not None:
            stack[i] = (tid, counter - weight, shared, fb)
        if not shared:
            break
        i -= 1
    while len(stack) > 1 and stack[-1][1] is not None and stack[-1][1] <= 0:
        stack.pop()
    return stack


def _frames_for(params: tuple[SwitchParam, ...], owner: str = "") -> list[_Frame]:
    frames: list[_Frame] = []
    for p in reversed(params):
        if p.table_id == RETURN:
            frames.append((f"{RETURN}@{owner}", None, False, None))
        else:
            frames.append((p.table_id, p.stop.count, p.shared, p.stop.fallback))
    return frames


def encode(
    text: str,
    tables: TableSet,
    *,
    end_terminated: bool = True,
    ends: int = 1,
    verify: bool = True,
) -> EncodeResult:
    """Encode script ``text``; raises ``EncodeError`` when it cannot.

    ``ends`` is how many end tokens an end-terminated string holds (a block's
    strings per pointer): an end token may be emitted before the end of the
    text ``ends - 1`` times, each starting the decoder's frame stack afresh,
    and with ``ends`` above one the text must hold exactly that many.
    """
    try:
        atoms = _atoms(text)
    except ValueError as exc:
        raise EncodeError(str(exc), 0, text[:20]) from None
    indexes = {tid: _index(t) for tid, t in tables.tables.items()}
    heuristic = min((i.min_bits_per_atom for i in indexes.values()), default=1.0)
    n = len(atoms)
    root: _Frame = (tables.start.id, None, False, None)
    start_state = (0, (root,), (), 0, 0)
    max_chain = len(tables.tables)
    tie = count()
    heap: list[tuple[float, int, int, tuple, str, bool]] = []
    heapq.heappush(heap, (heuristic * n, next(tie), 0, start_state, "", False))
    closed: set[tuple] = set()
    farthest = 0

    while heap:
        _, _, cost, state, bits, last_end = heapq.heappop(heap)
        if state in closed:
            continue
        closed.add(state)
        pos, stack, forbidden, chain, used = state
        farthest = max(farthest, pos)
        if pos == n and not any(f[3] is not None for f in stack) and not forbidden:
            if end_terminated and ends > 1 and used != ends:
                raise EncodeError(
                    f"the string holds {used} end token(s); the block reads "
                    f"{ends} per pointer",
                    None,
                    text[:40],
                )
            result = EncodeResult(bits, last_end)
            if verify:
                _verify(result, atoms, text, tables, end_terminated, ends)
            return result
        for npos, nstack, nforbidden, emitted, is_end in _successors(
            atoms,
            pos,
            stack,
            forbidden,
            tables,
            indexes,
            end_terminated,
            ends,
            used,
            chain,
            max_chain,
        ):
            nchain = 0 if npos > pos else chain + 1
            nstate = (npos, tuple(nstack), nforbidden, nchain, used + is_end)
            if nstate in closed:
                continue
            ncost = cost + len(emitted)
            priority = ncost + heuristic * (n - npos)
            heapq.heappush(
                heap, (priority, next(tie), ncost, nstate, bits + emitted, is_end)
            )

    context = "".join(
        a if isinstance(a, str) else f"[{a.label}]"
        for a in atoms[max(0, farthest - 10) : farthest + 10]
    )
    raise EncodeError(
        f"unable to encode; best attempt failed at position {farthest}",
        farthest,
        context,
    )


def _successors(
    atoms,
    pos,
    stack,
    forbidden,
    tables: TableSet,
    indexes,
    end_terminated,
    ends,
    used,
    chain,
    max_chain,
):
    n = len(atoms)
    tid, counter, shared, fallback = stack[-1]

    def emit(bits: str, weight: int, new_stack=None, push=(), is_end=False, advance=1):
        nf = _forbid(forbidden, bits)
        if nf is None:
            return None
        if fallback is not None and bits:
            if bits.startswith(fallback):
                return None
            if fallback.startswith(bits):
                nf = nf + (fallback[len(bits) :],)
        base = list(stack) if new_stack is None else new_stack
        base = _count(base, weight) if weight else base
        base = base + list(push)
        return (pos + advance, base, nf, bits, is_end)

    out = []
    # A return frame at the top: leave it and the frame it was matched in.
    if tid.startswith(f"{RETURN}@"):
        owner = tid.split("@", 1)[1]
        new_stack = list(stack[:-1])
        i = innermost_index(new_stack, lambda f: f[0] == owner)
        if i is not None:
            del new_stack[i:]
        elif pos < n:
            return out  # the string would end before its text does
        else:
            new_stack = new_stack[:1]
        return [(pos, new_stack, forbidden, "", False)]
    # Closing a fallback frame: its bits, at any position (always emitted).
    if fallback is not None and len(stack) > 1:
        nf = _forbid(forbidden, fallback)
        if nf is not None:
            out.append((pos, list(stack[:-1]), nf, fallback, False))
    if tid in (RAW, BITS):
        if pos < n and isinstance(atoms[pos], CodeRef):
            ref = atoms[pos]
            if (tid == RAW and ref.is_raw_byte) or (tid == BITS and ref.is_raw_bits):
                s = emit(ref.raw_bits(), 1)
                if s:
                    out.append(s)
        return out

    table = tables.table(tid)
    idx = indexes[tid]
    # A return entry closes the innermost frame of this table.
    if len(stack) > 1:
        for ret in idx.returns:
            new_stack = list(stack)
            i = innermost_index(new_stack, lambda f: f[0] == tid)
            if i is not None:
                del new_stack[i:]
            s = emit(ret.bits, 0, new_stack=new_stack, advance=0)
            if s:
                # The return counts its weight in the frame it was matched in
                # before popping; charge it to the popped frame is moot.
                out.append(s)
    # Silent switches: zero-width, bounded so chains cannot loop.
    if chain < max_chain:
        for entry in idx.silent:
            s = emit(
                entry.bits, entry.weight, push=_frames_for(entry.params, tid), advance=0
            )
            if s:
                out.append(_with_longer(s, idx, entry))
    if pos >= n:
        return out
    atom = atoms[pos]
    if isinstance(atom, CodeRef) and (atom.is_raw_byte or atom.is_raw_bits):
        raw = atom.raw_bits()
        # Unmatched data in a table frame: the decoder must find no entry here.
        if not any(
            other.startswith(raw) or raw.startswith(other) for other in table.entries
        ):
            s = emit(raw, 0)
            if s:
                out.append(s)
        return out
    key = _atom_key(atom)
    for entry_atoms, entry in idx.by_first.get(key, ()):
        k = len(entry_atoms)
        if pos + k > n or not all(
            _atoms_equal(entry_atoms[i], atoms[pos + i]) for i in range(k)
        ):
            continue
        interior_end = entry.kind is EntryKind.END and pos + k != n
        if interior_end and end_terminated and used + 1 >= ends:
            continue
        push = _frames_for(entry.params, tid) if entry.kind is EntryKind.SWITCH else ()
        s = emit(
            entry.bits,
            entry.weight,
            # The decoder starts the next run from the root frame.
            new_stack=[stack[0]] if interior_end and end_terminated else None,
            push=push,
            is_end=entry.kind is EntryKind.END,
            advance=k,
        )
        if s:
            out.append(_with_longer(s, idx, entry))
    if isinstance(atom, CodeRef):
        entry = idx.codes.get(atom.label)
        if entry is not None:
            try:
                values = operand_values(entry, atom.words)
            except ValueError:
                return out
            s = emit(bits_for(entry, values), entry.weight)
            if s:
                out.append(_with_longer(s, idx, entry))
    return out


def _with_longer(succ, idx: _Index, entry: Entry):
    npos, nstack, nf, bits, is_end = succ
    longer = idx.longer.get(entry.bits)
    if longer:
        nf = nf + longer
    return npos, nstack, nf, bits, is_end


def _verify(
    result: EncodeResult,
    atoms,
    text: str,
    tables: TableSet,
    end_terminated: bool,
    ends: int = 1,
) -> None:
    data = Bits(result.data)
    rules = DecodeRules(end_terminated=end_terminated, limit_bit=len(result.bits))
    run = decode_run(data, tables, 0, rules, runs=max(ends, 1) if end_terminated else 1)
    got = render(run.tokens).replace("\n", "")
    want = "".join(a if isinstance(a, str) else _ref_text(a) for a in atoms)
    from mapchar.core.tokens import escape_text

    want_cmp = "".join(
        escape_text(a) if isinstance(a, str) else _ref_text(a) for a in atoms
    )
    if got != want_cmp or run.end_bit != len(result.bits):
        raise EncodeError(
            f"encoding does not decode back to the text (got {got!r})", None, want[:40]
        )
    if (
        end_terminated
        and result.ends_with_end
        and run.ended_by is not EndedBy.END_TOKEN
    ):
        raise EncodeError("the end token did not end the string", None, want[:40])


def _ref_text(ref: CodeRef) -> str:
    if ref.words:
        return f"[{ref.label} {' '.join(ref.words)}]"
    return f"[{ref.label}]"
