"""The native table-file grammar: ``@mapchar table 1`` files.

See ``docs/plan/table-format.md``. One line per entry; ``#`` comments; ``@``
directives; ``/`` end, ``$`` operands, ``!`` table control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mapchar.core.bits import hex_to_bits
from mapchar.core.errors import TableError
from mapchar.core.notices import Notice
from mapchar.core.table import (
    BITS,
    ID_PATTERN,
    LABEL_PATTERN,
    RAW,
    Entry,
    EntryKind,
    OperandSpec,
    Stop,
    SwitchParam,
    Table,
)

HEADER = "@mapchar table 1"

_ENTRY = re.compile(
    r"^(?P<prefix>[/$!]?)(?P<key>%[01]+|[0-9A-Fa-f]+)(?:<(?P<weight>-?\d+)>)?=(?P<rhs>.*)$"
)
_LABEL_HEAD = re.compile(r"^\[(" + LABEL_PATTERN.pattern + r")\]")
_PARAM = re.compile(
    r"^@(?P<table>[A-Za-z0-9_.-]+):(?P<stop>\*|\d+|\$[0-9A-Fa-f]+|%[01]+)(?P<shared>\+?)$"
)


@dataclass
class TableFile:
    """The result of loading one file: its tables in order, plus notices."""

    tables: list[Table]
    notices: list[Notice] = field(default_factory=list)
    dialect: str = "native"


def split_lines(text: str) -> list[str]:
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def is_native(text: str) -> bool:
    """Whether the first non-blank, non-comment line is the native header."""
    for line in split_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped == HEADER
    return False


def parse_native(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    tables: list[Table] = []
    current: Table | None = None
    seen_header = False

    def table_for_entries(line_no: int) -> Table:
        nonlocal current
        if current is None:
            current = Table(default_id)
            tables.append(current)
        return current

    for n, raw in enumerate(split_lines(text), start=1):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not seen_header:
            if stripped != HEADER:
                raise TableError(f"expected {HEADER!r} as the first line", path, n)
            seen_header = True
            continue
        if stripped.startswith("@"):
            parts = stripped[1:].split(None, 1)
            keyword = parts[0] if parts else ""
            arg = parts[1].strip() if len(parts) > 1 else ""
            if keyword == "table":
                if not ID_PATTERN.fullmatch(arg):
                    raise TableError(f"invalid table id {arg!r}", path, n)
                if any(t.id == arg for t in tables):
                    raise TableError(f"table {arg!r} defined twice", path, n)
                current = Table(arg)
                tables.append(current)
            elif keyword == "charset":
                if not arg:
                    raise TableError("@charset needs a name", path, n)
                table_for_entries(n).charset = arg
            elif keyword == "mapchar":
                raise TableError("header repeated", path, n)
            else:
                raise TableError(f"unknown directive @{keyword}", path, n)
            continue
        try:
            entry = parse_entry(line)
        except ValueError as exc:
            raise TableError(str(exc), path, n) from None
        try:
            table_for_entries(n).add(entry)
        except TableError as exc:
            raise TableError(exc.message, path, n) from None
    if not seen_header:
        raise TableError(f"missing {HEADER!r} header", path)
    return TableFile(tables)


def parse_key(key: str) -> str:
    if key.startswith("%"):
        return key[1:]
    return hex_to_bits(key)


def parse_entry(line: str) -> Entry:
    """Parse one entry line of the native grammar. Raises ValueError."""
    m = _ENTRY.match(line)
    if not m:
        raise ValueError(f"not an entry: {line!r}")
    prefix, key, weight, rhs = m.group("prefix", "key", "weight", "rhs")
    bits = parse_key(key)
    w = int(weight) if weight is not None else 1
    if prefix == "":
        _check_text(rhs)
        return Entry(bits, EntryKind.TEXT, rhs, w)
    if prefix == "/":
        _check_text(rhs)
        return Entry(bits, EntryKind.END, rhs, w)
    if prefix == "$":
        label, rest = _take_label(rhs, "$")
        if not rest.startswith(","):
            raise ValueError("operand entry needs ',spec' after the label")
        specs = [s.strip() for s in rest[1:].split(",")]
        if not specs or any(not s for s in specs):
            raise ValueError("empty operand spec")
        operands = tuple(OperandSpec.parse(s) for s in specs)
        return Entry(bits, EntryKind.CODE, label, w, operands=operands)
    # prefix == "!"
    if rhs.strip() == "return":
        return Entry(bits, EntryKind.RETURN, "", w)
    if rhs.strip().startswith("return"):
        raise ValueError("'return' takes no label or parameters")
    label, rest = _take_label(rhs, "!")
    words = rest.split()
    if not words:
        raise ValueError(f"switch [{label}] needs at least one parameter")
    return Entry(
        bits, EntryKind.SWITCH, label, w, params=tuple(map(parse_param, words))
    )


def _check_text(rhs: str) -> None:
    depth = 0
    i = 0
    while i < len(rhs):
        c = rhs[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            if depth:
                raise ValueError("nested '[' in text")
            depth = 1
        elif c == "]":
            if not depth:
                raise ValueError("stray ']' in text (write \\])")
            depth = 0
        i += 1
    if depth:
        raise ValueError("unclosed '[' in text (write \\[)")


def _take_label(rhs: str, prefix: str) -> tuple[str, str]:
    m = _LABEL_HEAD.match(rhs)
    if not m:
        raise ValueError(f"'{prefix}' entry needs a [label] after '='")
    return m.group(1), rhs[m.end() :]


def parse_param(word: str) -> SwitchParam:
    m = _PARAM.match(word)
    if not m:
        raise ValueError(f"bad switch parameter {word!r}")
    table, stop, shared = m.group("table", "stop", "shared")
    if stop == "*":
        s = Stop()
    elif stop.isdigit():
        s = Stop(count=int(stop))
    elif stop.startswith("$"):
        s = Stop(fallback=hex_to_bits(stop[1:]))
    else:
        s = Stop(fallback=stop[1:])
    return SwitchParam(table, s, bool(shared))


def format_key(bits: str) -> str:
    if len(bits) % 4 == 0:
        return "".join(
            format(int(bits[i : i + 4], 2), "X") for i in range(0, len(bits), 4)
        )
    return "%" + bits


def format_entry(entry: Entry) -> str:
    key = format_key(entry.bits)
    if entry.weight != 1:
        key += f"<{entry.weight}>"
    if entry.kind is EntryKind.TEXT:
        return f"{key}={entry.text}"
    if entry.kind is EntryKind.END:
        return f"/{key}={entry.text}"
    if entry.kind is EntryKind.CODE:
        specs = ",".join(o.spec() for o in entry.operands)
        return f"${key}=[{entry.text}],{specs}"
    if entry.kind is EntryKind.RETURN:
        return f"!{key}=return"
    params = " ".join(p.spec() for p in entry.params)
    return f"!{key}=[{entry.text}] {params}"


def write_native(tables: list[Table]) -> str:
    lines = [HEADER]
    for table in tables:
        lines.append("")
        lines.append(f"@table {table.id}")
        if table.charset != "none":
            lines.append(f"@charset {table.charset}")
        for entry in table.sorted_entries():
            lines.append(format_entry(entry))
    return "\n".join(lines) + "\n"


__all__ = [
    "BITS",
    "RAW",
    "TableFile",
    "format_entry",
    "is_native",
    "parse_entry",
    "parse_native",
    "parse_param",
    "write_native",
]
