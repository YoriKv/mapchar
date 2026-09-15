"""The decode engine: bytes to tokens through a table set.

A stack machine built to the replication notes of ``docs/abcde/bin2text.md``:
one frame per switch parameter, each with its own counter; fallback bits
checked before the frame's table; unmatched data as raw bytes; end of data
as a string end, never an error.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mapchar.core.bits import Bits, align_up
from mapchar.core.notices import Notice
from mapchar.core.table import (
    BITS,
    RAW,
    RETURN,
    Entry,
    OperandSpec,
    Stop,
    SwitchParam,
    Table,
    TableSet,
    TokenKind,
)
from mapchar.core.tokens import Token


class EndedBy(Enum):
    END_TOKEN = "end"
    LIMIT = "limit"
    DATA = "data"
    RETURN = "return"
    LINES = "lines"


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
    line_label: str = "line"
    """The block's line code; a token that is one renders with a line break."""
    max_lines: int = 0
    """Stop after this many line codes (0: never)."""


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
    pending: OperandSpec | None = None
    """A count still to be read from the data, the first time the frame is
    on top."""


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
    lines = 0

    def window(at: int, n: int) -> str:
        return _window(bits, at, n, limit, skips)

    while pos < limit:
        frame = stack[-1]

        # 0. A return frame at the top leaves the frame the switch was matched in.
        if frame.table_id == RETURN:
            stack.pop()
            owner = tables.tables.get(frame.owner or "")
            if owner is None or not _pop_table(stack, owner):
                return DecodeResult(tokens, pos, EndedBy.RETURN, notices)
            continue

        # 0.5 A count read from the data opens the frame and prints nothing;
        #     cut short by the end of the data, it is the raw bytes it was.
        if frame.pending is not None:
            spec, frame.pending = frame.pending, None
            chunk = window(pos, spec.bits)
            end = _advance(pos, len(chunk), skips)
            if len(chunk) < spec.bits:
                tokens.append(Token(chunk, pos, end, table_id=frame.table_id))
                notices.append(
                    Notice(
                        f"@{frame.table_id}:{spec.spec()} count cut short by the "
                        "end of the data",
                        offset=pos // 8,
                    )
                )
                pos = end
                stack.pop()
                continue
            tokens.append(
                Token(chunk, pos, end, table_id=frame.table_id, fallback=True)
            )
            pos = end
            frame.counter = spec.value_of(chunk)
            _pop_finished(stack)
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
        newline = entry.is_newline(rules.line_label)
        tokens.append(
            Token(
                entry.bits, start, pos, entry, operands, frame.table_id, newline=newline
            )
        )

        # 4. Count the match in this frame, and beneath it while shared.
        _count(stack, entry.weight)

        # 5. Kind-specific behaviour.
        if entry.kind is TokenKind.RETURN:
            if not _pop_table(stack, frame.table):
                return DecodeResult(tokens, pos, EndedBy.RETURN, notices)
            continue
        if entry.kind is TokenKind.END and rules.end_terminated:
            pos = _realign(pos, rules.realign)
            return DecodeResult(tokens, pos, EndedBy.END_TOKEN, notices)
        if newline:
            lines += 1
            if rules.max_lines and lines >= rules.max_lines:
                _pop_finished(stack)
                return DecodeResult(tokens, pos, EndedBy.LINES, notices)
        if entry.kind is TokenKind.SWITCH:
            _pop_finished(stack)
            # Innermost last, so the first parameter runs first.
            for param in reversed(entry.params):
                stack.append(_frame_for(param, tables, frame.table_id))
            continue
        _pop_finished(stack)

    ended = EndedBy.DATA if limit >= bits.length else EndedBy.LIMIT
    return DecodeResult(tokens, pos, ended, notices)


@dataclass
class RunResult:
    """Consecutive decode runs read as one string."""

    tokens: list[Token]
    end_bit: int
    starts: list[int] = field(default_factory=list)
    """The bit position each run began at."""
    notices: list[Notice] = field(default_factory=list)
    ended_by: EndedBy | None = None
    """How the last run ended; ``None`` when none ran."""


def decode_run(
    bits: Bits,
    tables: TableSet,
    start: int = 0,
    rules: DecodeRules = DEFAULT_RULES,
    *,
    runs: int | None = None,
    ends_only: bool = True,
) -> RunResult:
    """Decode consecutive runs from ``start``, each resuming where the last ended.

    ``runs`` is how many to read -- a block's strings per pointer -- and
    ``None`` reads on until the data runs out. Reading stops early when a run
    makes no progress, when it ends at the end of the data or at the limit,
    or, with ``ends_only``, when it ends any way other than an end token.

    A run that legitimately ends behind its start -- a backwards skip range --
    reads as no progress, so extraction keeps its own loop for that case.
    """
    tokens: list[Token] = []
    notices: list[Notice] = []
    starts: list[int] = []
    pos = start
    ended: EndedBy | None = None
    left = runs
    while left is None or left > 0:
        if runs is None and pos >= bits.length:
            break
        starts.append(pos)
        r = decode(bits, tables, pos, rules)
        tokens.extend(r.tokens)
        notices.extend(r.notices)
        ended = r.ended_by
        was, pos = pos, r.end_bit
        if left is not None:
            left -= 1
        if pos <= was or ended in (EndedBy.DATA, EndedBy.LIMIT):
            break
        if ends_only and ended is not EndedBy.END_TOKEN:
            break
    return RunResult(tokens, pos, starts, notices, ended)


def innermost_index(stack: Sequence[Any], match: Callable[[Any], bool]) -> int | None:
    """Index of the innermost frame above the root that ``match`` accepts.

    Leaving a table pops that frame and everything above it; the encoder
    keeps its own frame shape and does the same walk.
    """
    for i in range(len(stack) - 1, 0, -1):
        if match(stack[i]):
            return i
    return None


def _frame_for(param: SwitchParam, tables: TableSet, owner: str) -> _Frame:
    if param.table_id == RETURN:
        return _Frame(RETURN, None, Stop(), False, None, owner)
    table = None if param.table_id in (RAW, BITS) else tables.table(param.table_id)
    return _Frame(
        param.table_id,
        table,
        param.stop,
        param.shared,
        param.stop.count,
        pending=param.stop.operand,
    )


def _count(stack: list[_Frame], weight: int) -> None:
    i = len(stack) - 1
    if i == 0:
        return  # the root frame counts nothing
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
    i = innermost_index(stack, lambda f: f.table is table)
    if i is None:
        return False
    del stack[i:]
    _pop_finished(stack)
    return True


def _window(
    bits: Bits, at: int, n: int, limit: int, skips: list[tuple[int, int]]
) -> str:
    """``n`` bits from ``at``, spliced across skip ranges, cut at the limit."""
    if not skips:
        return bits.window(at, min(n, limit - at))
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


def _advance(pos: int, n: int, skips: list[tuple[int, int]]) -> int:
    """``n`` bits past ``pos``, jumping over any skip range on the way."""
    if not skips:
        return pos + n
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
    return align_up(pos, *realign)


def _read_operands(
    bits: Bits, entry: Entry, pos: int, limit: int, skips: list[tuple[int, int]]
) -> tuple[tuple[int, ...], int, bool]:
    """Read the entry's operands; ``short`` is set when the data ran out."""
    values: list[int] = []
    for spec in entry.operands:
        chunk = _window(bits, pos, spec.bits, limit, skips)
        if len(chunk) < spec.bits:
            return tuple(values), pos, True
        values.append(spec.value_of(chunk))
        pos = _advance(pos, spec.bits, skips)
    return tuple(values), pos, False


__all__ = [
    "DecodeResult",
    "DecodeRules",
    "EndedBy",
    "RunResult",
    "decode",
    "decode_run",
    "innermost_index",
]
