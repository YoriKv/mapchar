"""The romjuice dialect: ``docs/romjuice.md``."""

from __future__ import annotations

import re

from mapchar.core.notices import Level, Notice
from mapchar.core.table import Entry, OperandSpec, Stop, SwitchParam, Table, TokenKind
from mapchar.project.formats.legacy import _add_or_note, legacy_text
from mapchar.project.formats.table_native import TableFile
from mapchar.project.formats.textfile import split_lines

_RJ_HEX = re.compile(r"^[0-9A-Fa-f]+")


def _rj_key(text: str) -> tuple[str, int] | None:
    """romjuice's key rule: ``%x`` of the hex prefix, width ``⌊digits/2⌋``."""
    m = _RJ_HEX.match(text)
    if not m:
        return None
    digits = m.group(0)[:8]
    width = len(digits) // 2
    if width == 0 or width > 4:
        return None
    value = int(digits, 16)
    return format(value, f"0{width * 8}b")[-width * 8 :], width


def _rj_count(text: str) -> int | None:
    """``sscanf("%i")``: decimal, ``0x`` hex, or octal with a leading ``0``."""
    m = re.match(r"^\s*[-+]?(0[xX][0-9A-Fa-f]+|0[0-7]*|[1-9][0-9]*)", text)
    if not m:
        return None
    return int(m.group(0), 0)


def _rj_unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in ("n", "r"):
                out.append("\n")
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append("\\")
                out.append(nxt)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def read_romjuice(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    table = Table(default_id)
    notices: list[Notice] = []
    kanji: dict[str, list[tuple[str, int]]] = {}
    two_byte: dict[int, str] = {}
    pending: list[tuple[int, Entry]] = []

    def note(n: int, msg: str) -> None:
        notices.append(Notice(f"line {n}: {msg}", Level.INFO))

    for n, line in enumerate(split_lines(text), start=1):
        if not line or "=" not in line and line[:1] != "!":
            continue
        if line[0] == "!":
            # romjuice swaps to a second table file the app has no way to name,
            # which is what romjuice itself does without one.
            note(n, "swap entry dropped: no second table given")
            continue
        if line[0] == "=":
            continue
        if line[0] == "@":
            key = _rj_key(line[1:])
            eq = line.find("=")
            if key is None or eq < 0:
                continue
            bits, _ = key
            rhs = line[eq + 1 :]
            count = _rj_count(rhs)
            comma = rhs.find(",")
            base = (
                int(m.group(0), 16)
                if (m := _RJ_HEX.match(rhs[comma + 1 :].strip())) and comma >= 0
                else 0
            )
            if not count or not base:
                note(n, "kanji entry with a zero count or base dropped")
                continue
            tid = f"kanji_{base:X}"
            kanji.setdefault(tid, []).append((bits, base))
            entry = Entry(
                bits,
                TokenKind.SWITCH,
                f"[{tid}]",
                params=(SwitchParam(tid, Stop(count=count)),),
            )
            pending.append((n, entry))
            continue
        if line[0] == "$":
            key = _rj_key(line[1:])
            eq = line.find("=")
            if key is None or eq < 0:
                continue
            bits, width = key
            count = _rj_count(line[eq + 1 :])
            if not count:
                note(n, "linked entry with a zero count dropped")
                continue
            label = f"raw_{int(bits, 2):0{width * 2}X}"
            entry = Entry(
                bits, TokenKind.CODE, label, operands=(OperandSpec("bytes", count * 8),)
            )
            pending.append((n, entry))
            continue
        key = _rj_key(line)
        if key is None:
            note(n, "line without a hex key ignored")
            continue
        bits, width = key
        eq = line.find("=")
        value = _rj_unescape(line[eq + 1 :])
        if width == 2:
            two_byte[int(bits, 2)] = value
        pending.append((n, Entry(bits, TokenKind.TEXT, legacy_text(value))))

    for n, entry in pending:
        _add_or_note(table, entry, n, notices)

    extra: list[Table] = []
    for tid, refs in kanji.items():
        base = refs[0][1]
        kt = Table(tid)
        for b in range(256):
            text_value = two_byte.get(base + b)
            if text_value is not None:
                kt.add(Entry(format(b, "08b"), TokenKind.TEXT, legacy_text(text_value)))
        if not kt.entries:
            notices.append(Notice(f"kanji table {tid} has no entries", Level.WARNING))
        extra.append(kt)
    return TableFile(table, notices, "romjuice", extra)
