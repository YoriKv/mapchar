"""The native table-file grammar: ``@mapchar table 1`` files.

See ``docs/plan/table-format.md``. One line per entry; ``#`` comments; ``@``
directives; ``/`` end, ``$`` operands, ``!`` table control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mapchar.core.bits import format_key, hex_to_bits
from mapchar.core.errors import TableError
from mapchar.core.notices import Notice
from mapchar.core.table import (
    BITS,
    ID_PATTERN,
    LABEL_PATTERN,
    RAW,
    RETURN,
    Entry,
    OperandSpec,
    SwitchParam,
    Table,
    TokenKind,
    parse_stop,
)
from mapchar.core.text import nfc, split_lines

HEADER = "@mapchar table 1"

_ENTRY = re.compile(
    r"^(?P<prefix>[/$!]?)(?P<key>%[01]+|[0-9A-Fa-f]+)(?:<(?P<weight>-?\d+)>)?=(?P<rhs>.*)$"
)
_LABEL_HEAD = re.compile(r"^\[(" + LABEL_PATTERN.pattern + r")\]")
_PARAM = re.compile(
    r"^@(?P<table>" + ID_PATTERN.pattern + r"):"
    r"(?P<stop>\*|\d+|\$[0-9A-Fa-f]+|%[01]+)(?P<shared>\+?)$"
    r"|^return$"
)


@dataclass
class TableFile:
    """The result of loading one file: its table, plus notices."""

    table: Table
    notices: list[Notice] = field(default_factory=list)
    dialect: str = "native"
    extra_tables: list[Table] = field(default_factory=list)
    """Tables a legacy conversion made beyond the file's own.

    A romjuice kanji entry generates a table, and an abcde file may hold several;
    each becomes a table entry of its own, with no file until it is saved.
    """
    encoding: str = "utf-8"
    """The encoding the file was decoded as; a legacy table is often
    ``cp932`` (:func:`mapchar.core.text.read_text_any` decides)."""
    end_marker: str | None = None
    """The text an Atlas table spelled its end token with, when it named one.

    Atlas writes the end token as an ordinary entry with a chosen text, and a
    script dumped for that table has to spell it the same way; no other dialect
    sets this.
    """

    @property
    def tables(self) -> list[Table]:
        """The file's own table, then every extra one."""
        return [self.table, *self.extra_tables]


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
    """The one table a native file holds, named ``default_id`` unless a
    ``@table`` line names it."""
    table = Table(default_id)
    seen_header = False
    named = False
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
                arg = nfc(arg)
                if not ID_PATTERN.fullmatch(arg):
                    raise TableError(f"invalid table id {arg!r}", path, n)
                if named:
                    raise TableError("a table file holds one table", path, n)
                table.id = arg
                named = True
            elif keyword == "charset":
                if not arg:
                    raise TableError("@charset needs a name", path, n)
                table.charset = arg
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
            table.add(entry)
        except TableError as exc:
            raise TableError(exc.message, path, n) from None
    if not seen_header:
        raise TableError(f"missing {HEADER!r} header", path)
    return TableFile(table)


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
        return Entry(bits, TokenKind.TEXT, rhs, w)
    if prefix == "/":
        _check_text(rhs)
        return Entry(bits, TokenKind.END, rhs, w)
    if prefix == "$":
        label, rest = _take_label(rhs, "$")
        if not rest.startswith(","):
            raise ValueError("operand entry needs ',spec' after the label")
        specs = [s.strip() for s in rest[1:].split(",")]
        if not specs or any(not s for s in specs):
            raise ValueError("empty operand spec")
        operands = tuple(OperandSpec.parse(s) for s in specs)
        return Entry(bits, TokenKind.CODE, label, w, operands=operands)
    # prefix == "!"
    if rhs.strip() == "return":
        return Entry(bits, TokenKind.RETURN, "", w)
    if rhs.strip().startswith("return"):
        raise ValueError("'return' takes no label or parameters")
    text, params = _split_switch(rhs)
    if not params:
        raise ValueError("switch entry needs at least one parameter")
    if RETURN in params[:-1]:
        raise ValueError("'return' must be the last parameter")
    _check_text(text)
    return Entry(
        bits, TokenKind.SWITCH, text, w, params=tuple(map(parse_param, params))
    )


def _split_switch(rhs: str) -> tuple[str, list[str]]:
    """The text of a switch entry and its trailing parameters.

    Parameters are the whitespace-separated words at the end that parse as
    parameters; whatever precedes them, minus one separating space, is the
    text, which may be empty.
    """
    words = rhs.split(" ")
    params: list[str] = []
    while words and words[-1] and _PARAM.match(words[-1]):
        params.insert(0, words.pop())
    return " ".join(words), params


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
    if word == RETURN:
        return SwitchParam(RETURN)
    m = _PARAM.match(word)
    if not m:
        raise ValueError(f"bad switch parameter {word!r}")
    table, stop, shared = m.group("table", "stop", "shared")
    return SwitchParam(table, parse_stop(stop), bool(shared))


def format_entry(entry: Entry) -> str:
    key = format_key(entry.bits)
    if entry.weight != 1:
        key += f"<{entry.weight}>"
    if entry.kind is TokenKind.TEXT:
        return f"{key}={entry.text}"
    if entry.kind is TokenKind.END:
        return f"/{key}={entry.text}"
    if entry.kind is TokenKind.CODE:
        specs = ",".join(o.spec() for o in entry.operands)
        return f"${key}=[{entry.text}],{specs}"
    if entry.kind is TokenKind.RETURN:
        return f"!{key}=return"
    params = " ".join(p.spec() for p in entry.params)
    return f"!{key}={entry.text} {params}"


def write_native(table: Table) -> str:
    lines = [HEADER, f"@table {table.id}"]
    if table.charset != "none":
        lines.append(f"@charset {table.charset}")
    lines.extend(format_entry(entry) for entry in table.sorted_entries())
    return "\n".join(lines) + "\n"


__all__ = [
    "BITS",
    "RAW",
    "TableFile",
    "format_entry",
    "format_key",
    "is_native",
    "parse_entry",
    "parse_native",
    "parse_param",
    "split_lines",
    "write_native",
]
