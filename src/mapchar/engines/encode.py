"""The encode engine: script text to bits through a table set.

A Dijkstra search over ``(position, frame stack, forbidden suffixes)`` for
the cheapest bit string that the decode engine turns back into the same
tokens. The forbidden suffixes are what keep longest-prefix decoding honest:
after an entry that is a proper prefix of a longer entry, the bits that would
complete the longer entry may not follow. Every result is verified by
decoding it.

What the search looks a table up in is
:mod:`mapchar.engines.encode_index`, and what it says when it finds nothing
is :mod:`mapchar.engines.encode_why`.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import count

from mapchar.core.bits import Bits, bits_to_bytes
from mapchar.core.errors import EncodeError
from mapchar.core.table import (
    BITS,
    RAW,
    RETURN,
    SwitchParam,
    TableEntry,
    TableSet,
    TokenKind,
)
from mapchar.core.text import nfc
from mapchar.core.tokens import (
    CodeRef,
    bits_for,
    escape_text,
    operand_values,
    render,
)
from mapchar.engines.decode import DecodeRules, EndedBy, decode_run, innermost_index
from mapchar.engines.encode_index import (
    Index,
    atom_key,
    atoms_equal,
    atoms_of,
    extensions,
    index_of,
    layers_of,
    ref_text,
)
from mapchar.engines.encode_why import why

_Frame = tuple[str, int | None, bool, str | None, tuple | None, bool]
"""``(table_id, counter, shared, fallback_bits, count, through)``; counter None is
unlimited; ``through`` lets bits the table does not match fall to the frame
beneath. ``count`` is ``(spec, at, consumed)`` for a frame whose count the
data carries: the operand it is written as, the bit offset it was written at
(``None`` until the frame is on top and writes its placeholder) and the weight
matched so far, which closing the frame writes into the placeholder."""


@dataclass
class EncodeResult:
    bits: str
    ends_with_end: bool
    """The last token is an end token."""

    @property
    def data(self) -> bytes:
        return bits_to_bytes(self.bits)


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
        tid, counter, shared, fb, cnt, through = stack[i]
        if counter is not None:
            stack[i] = (tid, counter - weight, shared, fb, cnt, through)
        elif cnt is not None:
            spec, at, consumed = cnt
            stack[i] = (
                tid,
                counter,
                shared,
                fb,
                (spec, at, consumed + weight),
                through,
            )
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
            frames.append((f"{RETURN}@{owner}", None, False, None, None, False))
        else:
            operand = p.stop.operand
            count = (operand, None, 0) if operand is not None else None
            frames.append(
                (
                    p.table_id,
                    p.stop.count,
                    p.shared,
                    p.stop.fallback,
                    count,
                    p.through,
                )
            )
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
        atoms = atoms_of(text)
    except ValueError as exc:
        raise EncodeError(str(exc), 0, text[:20]) from None
    indexes = {tid: index_of(t) for tid, t in tables.tables.items()}
    heuristic = min((i.min_bits_per_atom for i in indexes.values()), default=1.0)
    n = len(atoms)
    root: _Frame = (tables.start.id, None, False, None, None, False)
    start_state = (0, (root,), (), 0, 0)
    max_chain = len(tables.tables)
    tie = count()
    heap: list[tuple[float, int, int, tuple, str, bool]] = []
    heapq.heappush(heap, (heuristic * n, next(tie), 0, start_state, "", False))
    closed: set[tuple] = set()
    farthest = 0
    far_stack: tuple[_Frame, ...] = (root,)
    """The frames in use where the search got furthest: what says whether a
    character has no entry at all or one in a table nothing switches to."""

    while heap:
        _, _, cost, state, bits, last_end = heapq.heappop(heap)
        if state in closed:
            continue
        closed.add(state)
        pos, stack, forbidden, chain, used = state
        if pos > farthest:
            farthest, far_stack = pos, stack
        if (
            pos == n
            and not any(f[3] is not None or f[4] is not None for f in stack)
            and not forbidden
        ):
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
        for succ in _successors(
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
            len(bits),
        ):
            npos, nstack, nforbidden, emitted, is_end, *patch = succ
            nchain = 0 if npos > pos else chain + 1
            nstate = (npos, tuple(nstack), nforbidden, nchain, used + is_end)
            if nstate in closed:
                continue
            nbits = bits
            if patch:
                # A count frame closing writes what it matched into the
                # placeholder it left when it opened.
                at, value = patch[0]
                nbits = nbits[:at] + value + nbits[at + len(value) :]
            ncost = cost + len(emitted)
            priority = ncost + heuristic * (n - npos)
            heapq.heappush(
                heap, (priority, next(tie), ncost, nstate, nbits + emitted, is_end)
            )

    context = nfc(
        "".join(
            a if isinstance(a, str) else f"[{a.label}]"
            for a in atoms[max(0, farthest - 10) : farthest + 10]
        )
    )
    reason = why(atoms, farthest, far_stack, tables, indexes)
    # Where it failed, but only when the context is not the whole text: on a
    # short string the message already names everything there is to name.
    if context.strip() and (farthest > 10 or farthest + 10 < n):
        reason = f'{reason} — near "{context}"'
    raise EncodeError(reason, farthest, context)


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
    bit_len,
):
    n = len(atoms)
    tid, counter, shared, fallback, cnt, through = stack[-1]

    def emit(
        bits: str,
        weight: int,
        new_stack=None,
        push=(),
        is_end=False,
        advance=1,
        shadows=(),
    ):
        nf = _forbid(forbidden, bits)
        if nf is None:
            return None
        # Bits taken from a table beneath: no table the frame falls through
        # on the way there may match them, now or once more bits follow.
        for shadow in shadows:
            if tables.table(shadow).match(bits) is not None:
                return None
            nf = nf + extensions(indexes[shadow], bits)
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
    # A count frame writes its count first: a placeholder the close fills in.
    # The decoder reads those bits blind, but a longer entry of the outer
    # table could still begin with the switch's bits plus the count; the
    # zeros stand in for the check here, and verification catches the rest.
    if cnt is not None and cnt[1] is None:
        spec, _, consumed = cnt
        zeros = "0" * spec.bits
        nf = _forbid(forbidden, zeros)
        if nf is None:
            return out
        placed = list(stack)
        placed[-1] = (
            tid,
            counter,
            shared,
            fallback,
            (spec, bit_len, consumed),
            through,
        )
        return [(pos, placed, nf, zeros, False)]
    # Closing a count frame: what it matched, written where the placeholder is.
    if cnt is not None:
        spec, at, consumed = cnt
        if 0 <= consumed < 1 << spec.bits:
            value = spec.bits_of(consumed)
            out.append((pos, list(stack[:-1]), forbidden, "", False, (at, value)))
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

    out.extend(
        _table_successors(
            atoms,
            pos,
            stack,
            tables,
            indexes,
            end_terminated,
            ends,
            used,
            chain,
            max_chain,
            emit,
        )
    )
    return out


def _table_successors(
    atoms,
    pos,
    stack,
    tables: TableSet,
    indexes,
    end_terminated,
    ends,
    used,
    chain,
    max_chain,
    emit,
):
    n = len(atoms)
    out = []
    layers = layers_of(stack, tables)
    for tid, shadows in layers:
        idx = indexes[tid]
        # A return entry closes the innermost frame of the table that holds it.
        if len(stack) > 1:
            for ret in idx.returns:
                new_stack = list(stack)
                i = innermost_index(new_stack, lambda f, tid=tid: f[0] == tid)
                if i is not None:
                    del new_stack[i:]
                s = emit(ret.bits, 0, new_stack=new_stack, advance=0, shadows=shadows)
                if s:
                    # The return counts its weight in the frame it was matched in
                    # before popping; charge it to the popped frame is moot.
                    out.append(s)
        # Silent switches: zero-width, bounded so chains cannot loop.
        if chain < max_chain:
            for entry in idx.silent:
                s = emit(
                    entry.bits,
                    entry.weight,
                    push=_frames_for(entry.params, tid),
                    advance=0,
                    shadows=shadows,
                )
                if s:
                    out.append(_with_longer(s, idx, entry))
    if pos >= n:
        return out
    atom = atoms[pos]
    if isinstance(atom, CodeRef) and (atom.is_raw_byte or atom.is_raw_bits):
        raw = atom.raw_bits()
        # Unmatched data in a table frame: the decoder must find no entry here,
        # in the frame's table or any it falls through to.
        if not any(
            other.startswith(raw) or raw.startswith(other)
            for tid, _ in layers
            for other in tables.table(tid).entries
        ):
            s = emit(raw, 0)
            if s:
                out.append(s)
        return out
    key = atom_key(atom)
    for tid, shadows in layers:
        idx = indexes[tid]
        for entry_atoms, entry in idx.by_first.get(key, ()):
            k = len(entry_atoms)
            if pos + k > n or not all(
                atoms_equal(entry_atoms[i], atoms[pos + i]) for i in range(k)
            ):
                continue
            interior_end = entry.kind is TokenKind.END and pos + k != n
            if interior_end and end_terminated and used + 1 >= ends:
                continue
            push = (
                _frames_for(entry.params, tid) if entry.kind is TokenKind.SWITCH else ()
            )
            s = emit(
                entry.bits,
                entry.weight,
                # The decoder starts the next run from the root frame.
                new_stack=[stack[0]] if interior_end and end_terminated else None,
                push=push,
                is_end=entry.kind is TokenKind.END,
                advance=k,
                shadows=shadows,
            )
            if s:
                out.append(_with_longer(s, idx, entry))
        if isinstance(atom, CodeRef):
            entry = idx.codes.get(atom.label)
            if entry is not None:
                try:
                    # OverflowError as well: an operand too big for its spec is
                    # a code this table cannot write, not a crash.
                    bits = bits_for(entry, operand_values(entry, atom.words))
                except (ValueError, OverflowError):
                    continue
                s = emit(bits, entry.weight, shadows=shadows)
                if s:
                    out.append(_with_longer(s, idx, entry))
    return out


def _with_longer(succ, idx: Index, entry: TableEntry):
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
    want = "".join(a if isinstance(a, str) else ref_text(a) for a in atoms)
    want_cmp = "".join(
        escape_text(a) if isinstance(a, str) else ref_text(a) for a in atoms
    )
    if not _same_text(nfc(got), nfc(want_cmp), tables) or run.end_bit != len(
        result.bits
    ):
        raise EncodeError(
            f"encoding does not decode back to the text (got {got!r})", None, want[:40]
        )
    if (
        end_terminated
        and result.ends_with_end
        and run.ended_by is not EndedBy.END_TOKEN
    ):
        raise EncodeError("the end token did not end the string", None, want[:40])


def _same_text(got: str, want: str, tables: TableSet) -> bool:
    """Whether the decode ``got`` is the text that was encoded.

    An alias encodes as a code whose own text is something else (the yen sign
    on Shift-JIS ``5C`` decodes as a backslash), so where the two differ the
    aliases of the table set are allowed to stand in.
    """
    if got == want:
        return True
    aliases = {
        text: entry.text
        for table in tables.tables.values()
        for text, bits in table.aliases.items()
        if (entry := table.entries.get(bits)) is not None
    }
    g = w = 0
    while g < len(got) and w < len(want):
        if got[g] == want[w]:
            g, w = g + 1, w + 1
            continue
        for text, entry_text in aliases.items():
            if want.startswith(text, w) and got.startswith(entry_text, g):
                g, w = g + len(entry_text), w + len(text)
                break
        else:
            return False
    return g == len(got) and w == len(want)
