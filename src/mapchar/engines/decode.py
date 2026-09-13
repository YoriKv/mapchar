"""The decode engine: bytes to tokens through a table set.

A stack machine built to the replication notes of ``docs/abcde/bin2text.md``:
one frame per switch parameter, each with its own counter; fallback bits
checked before the frame's table; unmatched data as raw bytes; end of data
as a string end, never an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mapchar.core.bits import Bits
from mapchar.core.notices import Notice
from mapchar.core.table import (
    BITS,
    RAW,
    RETURN,
    Entry,
    EntryKind,
    Stop,
    SwitchParam,
    Table,
    TableSet,
)
from mapchar.core.tokens import Token


class EndedBy(Enum):
    END_TOKEN = "end"
    LIMIT = "limit"
    DATA = "data"
    RETURN = "return"


@dataclass(frozen=True)
class DecodeRules:
    end_terminated: bool = True
    """Stop at the first end token."""
    limit_bit: int | None = None
    """Exclusive bit position the string may not read past."""
    skips: tuple[tuple[int, int], ...] = ()
    """``(from_bit, to_bit)``: reading ``from_bit`` continues at ``to_bit``."""
    realign: tuple[int, int] = (0, 0)
    """``(multiple, offset)`` in bits, applied after an end token."""


@dataclass
class DecodeResult:
    tokens: list[Token]
    end_bit: int
    ended_by: EndedBy
    notices: list[Notice] = field(default_factory=list)


@dataclass
class _Frame:
    table_id: str
    table: Table | None
    """``None`` for the ``raw`` and ``bits`` pseudo tables."""
    stop: Stop
    shared: bool
    counter: int | None
    owner: str | None = None
    """For a return frame: the table whose innermost frame it leaves."""


DEFAULT_RULES = DecodeRules()


def decode(
    bits: Bits, tables: TableSet, start_bit: int, rules: DecodeRules = DEFAULT_RULES
) -> DecodeResult:
    limit = (
        bits.length if rules.limit_bit is None else min(rules.limit_bit, bits.length)
    )
    skips = sorted(rules.skips)
    tokens: list[Token] = []
    notices: list[Notice] = []
    root = _Frame(tables.start.id, tables.start, Stop(), False, None)
    stack: list[_Frame] = [root]
    pos = _follow_skips(start_bit, skips)

    def window(at: int, n: int) -> str:
        """``n`` bits from ``at``, spliced across skip ranges, cut at the limit."""
        out = ""
        cur = at
        while len(out) < n and cur < limit:
            want = n - len(out)
            nxt = next((s for s, e in skips if cur <= s < cur + want and e != s), None)
            take = min(want, limit - cur, (nxt - cur) if nxt is not None else want)
            if take > 0:
                out += bits.window(cur, take)
                cur += take
            if nxt is not None and cur == nxt:
                cur = next(e for s, e in skips if s == nxt)
            elif take <= 0:
                break
        return out

    while pos < limit:
        frame = stack[-1]

        # 0. A return frame at the top leaves the frame the switch was matched in.
        if frame.table_id == RETURN:
            stack.pop()
            owner = tables.tables.get(frame.owner or "")
            if owner is None or not _pop_table(stack, owner):
                return DecodeResult(tokens, pos, EndedBy.RETURN, notices)
            continue

        # 1. Fallback bits close the frame and print nothing.
        fb = frame.stop.fallback
        if fb is not None and window(pos, len(fb)) == fb:
            end = _advance(pos, len(fb), skips)
            tokens.append(Token(fb, pos, end, table_id=frame.table_id, fallback=True))
            pos = end
            stack.pop()
            continue

        # 2. Raw frames take one byte or bit per match, each weighing 1.
        if frame.table is None:
            chunk = window(pos, 1 if frame.table_id == BITS else 8)
            end = _advance(pos, len(chunk), skips)
            tokens.append(Token(chunk, pos, end, table_id=frame.table_id))
            pos = end
            _count(stack, 1)
            _pop_finished(stack)
            continue

        # 3. Longest-prefix match in the frame's table; else one unmatched
        #    byte, which weighs nothing and does not disturb the frame.
        entry = frame.table.match(window(pos, frame.table.max_bits))
        if entry is None:
            chunk = window(pos, 8)
            end = _advance(pos, len(chunk), skips)
            tokens.append(Token(chunk, pos, end, table_id=frame.table_id))
            pos = end
            continue

        start = pos
        pos = _advance(pos, len(entry.bits), skips)
        operands: tuple[int, ...] = ()
        if entry.operands:
            operands, pos, short = _read_operands(bits, entry, pos, limit, skips)
            if short:
                notices.append(
                    Notice(
                        f"[{entry.text}] cut short by the end of the data",
                        offset=start // 8,
                    )
                )
        tokens.append(Token(entry.bits, start, pos, entry, operands, frame.table_id))

        # 4. Count the match in this frame, and beneath it while shared.
        _count(stack, entry.weight)

        # 5. Kind-specific behaviour.
        if entry.kind is EntryKind.RETURN:
            if not _pop_table(stack, frame.table):
                return DecodeResult(tokens, pos, EndedBy.RETURN, notices)
            continue
        if entry.kind is EntryKind.END and rules.end_terminated:
            pos = _realign(pos, rules.realign)
            return DecodeResult(tokens, pos, EndedBy.END_TOKEN, notices)
        if entry.kind is EntryKind.SWITCH:
            _pop_finished(stack)
            # Innermost last, so the first parameter runs first.
            for param in reversed(entry.params):
                stack.append(_frame_for(param, tables, frame.table_id))
            continue
        _pop_finished(stack)

    ended = EndedBy.DATA if limit >= bits.length else EndedBy.LIMIT
    return DecodeResult(tokens, pos, ended, notices)


def _frame_for(param: SwitchParam, tables: TableSet, owner: str) -> _Frame:
    if param.table_id == RETURN:
        return _Frame(RETURN, None, Stop(), False, None, owner)
    table = None if param.table_id in (RAW, BITS) else tables.table(param.table_id)
    return _Frame(param.table_id, table, param.stop, param.shared, param.stop.count)


def _count(stack: list[_Frame], weight: int) -> None:
    i = len(stack) - 1
    while i >= 0:
        frame = stack[i]
        if frame.counter is not None:
            frame.counter -= weight
        if not frame.shared:
            break
        i -= 1


def _pop_finished(stack: list[_Frame]) -> None:
    """Pop every top frame whose counter is used up. Never pops the root."""
    while len(stack) > 1 and stack[-1].counter is not None and stack[-1].counter <= 0:
        stack.pop()


def _pop_table(stack: list[_Frame], table: Table) -> bool:
    """Pop the innermost frame of ``table`` and everything above it."""
    for i in range(len(stack) - 1, 0, -1):
        if stack[i].table is table:
            del stack[i:]
            _pop_finished(stack)
            return True
    return False


def _advance(pos: int, n: int, skips: list[tuple[int, int]]) -> int:
    """``n`` bits past ``pos``, jumping over any skip range on the way."""
    cur = _follow_skips(pos, skips)
    remaining = n
    while remaining > 0:
        nxt = next((s for s, e in skips if cur < s < cur + remaining and e != s), None)
        if nxt is None:
            break
        remaining -= nxt - cur
        cur = next(e for s, e in skips if s == nxt)
    return _follow_skips(cur + remaining, skips)


def _follow_skips(pos: int, skips: list[tuple[int, int]]) -> int:
    moved = True
    while moved:
        moved = False
        for start, stop in skips:
            if pos == start and stop > pos:
                pos = stop
                moved = True
    return pos


def _realign(pos: int, realign: tuple[int, int]) -> int:
    multiple, offset = realign
    if multiple <= 0:
        return pos
    rel = pos - offset
    if rel <= 0:
        return offset
    return -(-rel // multiple) * multiple + offset


def _read_operands(
    bits: Bits, entry: Entry, pos: int, limit: int, skips: list[tuple[int, int]]
) -> tuple[tuple[int, ...], int, bool]:
    """Read the entry's operands; ``short`` is set when the data ran out."""
    values: list[int] = []
    for spec in entry.operands:
        chunk = bits.window(pos, min(spec.bits, max(limit - pos, 0)))
        if len(chunk) < spec.bits:
            return tuple(values), pos, True
        values.append(spec.value_of(chunk))
        pos = _advance(pos, spec.bits, skips)
    return tuple(values), pos, False


__all__ = ["DecodeResult", "DecodeRules", "EndedBy", "RAW", "decode"]
