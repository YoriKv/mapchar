"""The native table-file grammar: ``@mapchar table 1`` files.

See ``docs/plan/table-format.md``. One line per entry; ``#`` comments; ``@``
directives; ``/`` end, ``$`` operands, ``!`` table control.

The spelling helpers every dialect shares live here too: :func:`parse_stop`
for a switch parameter's stop, and :func:`sanitize_id` and
:func:`sanitize_label` for names a file may write any way it likes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from mapchar.core.bits import format_key, hex_to_bits
from mapchar.core.errors import TableError
from mapchar.core.font import Effect
from mapchar.core.notices import Notice
from mapchar.core.table import (
    COUNT_SPECS,
    ID_PATTERN,
    LABEL_PATTERN,
    RETURN,
    TABLE_EFFECTS,
    Entry,
    OperandSpec,
    Stop,
    SwitchParam,
    Table,
    TokenKind,
)
from mapchar.core.text import nfc
from mapchar.project.formats.textfile import split_lines

HEADER = "@mapchar table 1"

KEY_HEAD = r"(?P<key>%[01]+|[0-9A-Fa-f]+)(?:<(?P<weight>-?\d+)>)?"
KEY_FIELD = KEY_HEAD + "="
"""How every dialect spells an entry's key: ``%bits`` or hex digits, an
optional ``<weight>``, then ``=``. The native grammar and abcde differ only in
what may precede it, so they share the middle."""

_ENTRY = re.compile(
    r"^(?P<prefix>[/$!]?)"
    + KEY_HEAD
    + r"(?:\{(?P<effect>[^{}=]*)})?="
    + r"(?P<rhs>.*)$"
)
"""The native entry: ``KEY_FIELD`` with an optional ``{effect}`` before ``=``."""
_LABEL_HEAD = re.compile(r"^\[(" + LABEL_PATTERN.pattern + r")\]")
_PARAM = re.compile(
    r"^@(?P<table>" + ID_PATTERN.pattern + r"):"
    r"(?P<stop>\*|\d+|" + "|".join(COUNT_SPECS) + r"|\$[0-9A-Fa-f]+|%[01]+)"
    r"(?P<shared>\+?)(?P<through>\|?)$"
    r"|^return$"
)


def parse_stop(word: str) -> Stop:
    """A switch parameter's stop as the table dialects write it.

    ``*`` and ``0`` are no stop at all, digits a weighted count, an unsigned
    operand spec a count read from the data, ``$hex`` and ``%bits`` fallback
    bits.
    """
    if word in ("*", "0"):
        return Stop()
    if word.isdigit():
        return Stop(count=int(word))
    if word in COUNT_SPECS:
        return Stop(operand=OperandSpec.parse(word))
    if word.startswith("$"):
        return Stop(fallback=hex_to_bits(word[1:]))
    return Stop(fallback=word[1:])


def sanitize_id(text: str) -> str:
    """``text`` as a table id: everything ``ID_PATTERN`` rejects becomes ``_``.

    Letters of every script pass, so two Japanese-named tables in one legacy
    file keep their own names instead of colliding on underscores.
    """
    return re.sub(r"[^\w.-]", "_", nfc(text))


def sanitize_label(text: str) -> str:
    """``text`` as a code label: an outer bracket pair off, no whitespace."""
    text = text.strip()
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        text = text[1:-1]
    fixed = re.sub(r"\s+", "_", text.strip())
    fixed = re.sub(r"[\[\]]", "_", fixed)
    if not fixed or fixed[0] in "$%":
        fixed = "_" + fixed
    return fixed if LABEL_PATTERN.fullmatch(fixed) else "_" + re.sub(r"\W", "_", fixed)


def comment_text(line: str) -> str:
    """What a ``#`` line says: the mark and one space after it dropped."""
    return line.strip()[1:].removeprefix(" ")


def comment_lines(comment: str) -> list[str]:
    """``comment`` as the ``#`` lines that spell it in a file."""
    return [f"# {line}".rstrip() for line in comment.split("\n")] if comment else []


class Comments:
    """The ``#`` lines a table file carries, as every dialect keeps them.

    The lines directly above an entry are that entry's own; a blank line, a
    directive or the end of the file gives whatever is pending to the file.
    """

    def __init__(self) -> None:
        self.pending: list[str] = []
        self.file: list[str] = []

    def add(self, line: str) -> None:
        """Collect one ``#`` line for whatever comes next."""
        self.pending.append(comment_text(line))

    def flush(self) -> None:
        """Nothing took the pending lines: they are the file's."""
        self.file.extend(self.pending)
        self.pending.clear()

    def take(self, entry: Entry) -> Entry:
        """``entry`` with the pending lines on it, which it consumes."""
        if not self.pending:
            return entry
        entry = replace(entry, comment="\n".join(self.pending))
        self.pending.clear()
        return entry

    def file_text(self) -> str:
        """Everything that fell to the file, as one comment."""
        return "\n".join(self.file)


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
    ``cp932`` (:func:`mapchar.project.formats.textfile.read_text_any` decides)."""

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
    comments = Comments()

    for n, raw in enumerate(split_lines(text), start=1):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.add(stripped)
            continue
        if not stripped:
            comments.flush()
            continue
        if not seen_header:
            comments.flush()
            if stripped != HEADER:
                raise TableError(f"expected {HEADER!r} as the first line", path, n)
            seen_header = True
            continue
        if stripped.startswith("@"):
            comments.flush()
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
            elif keyword == "include":
                arg = nfc(arg)
                if not ID_PATTERN.fullmatch(arg):
                    raise TableError(f"invalid table id {arg!r}", path, n)
                if arg in table.includes:
                    raise TableError(f"@include {arg} repeated", path, n)
                table.includes = (*table.includes, arg)
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
            table.add(comments.take(entry))
        except TableError as exc:
            raise TableError(exc.message, path, n) from None
    comments.flush()
    if not seen_header:
        raise TableError(f"missing {HEADER!r} header", path)
    table.comment = comments.file_text()
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
    prefix, key, weight, effect, rhs = m.group(
        "prefix", "key", "weight", "effect", "rhs"
    )
    entry = _parse_entry(prefix, parse_key(key), int(weight or 1), rhs)
    if effect is None:
        return entry
    if entry.kind is TokenKind.RETURN:
        raise ValueError("a return entry takes no effect")
    return replace(entry, effect=parse_effect(effect))


def parse_effect(word: str) -> Effect:
    """An entry's ``{effect}``: one of :data:`~mapchar.core.table.TABLE_EFFECTS`."""
    for effect in TABLE_EFFECTS:
        if word == effect.value:
            return effect
    names = ", ".join(e.value for e in TABLE_EFFECTS)
    raise ValueError(f"unknown effect {{{word}}}: an entry's effect is one of {names}")


def _parse_entry(prefix: str, bits: str, w: int, rhs: str) -> Entry:
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
    table, stop, shared, through = m.group("table", "stop", "shared", "through")
    return SwitchParam(table, parse_stop(stop), bool(shared), bool(through))


def parse_entry_lines(text: str) -> Entry:
    """An entry with its comment lines above it, as :func:`format_entry_lines`
    writes them. Raises ValueError."""
    lines = text.split("\n")
    comment = [comment_text(line) for line in lines[:-1] if line.strip()]
    if any(not line.strip().startswith("#") for line in lines[:-1] if line.strip()):
        raise ValueError("only comment lines may precede an entry")
    entry = parse_entry(lines[-1].strip())
    return replace(entry, comment="\n".join(comment)) if comment else entry


def format_entry_lines(entry: Entry) -> list[str]:
    """The entry's line, under its comment lines."""
    return [*comment_lines(entry.comment), format_entry(entry)]


def format_entry(entry: Entry) -> str:
    """The entry's own line, without its comment."""
    key = format_key(entry.bits)
    if entry.weight != 1:
        key += f"<{entry.weight}>"
    if entry.effect is not Effect.NONE:
        key += f"{{{entry.effect.value}}}"
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
    """The table as a native file: what it says beyond its charset and the
    tables it includes (:meth:`~mapchar.core.table.Table.own_entries`), each
    entry under its comment, the file's own comment under the header."""
    lines = [HEADER, *comment_lines(table.comment), f"@table {table.id}"]
    if table.charset != "none":
        lines.append(f"@charset {table.charset}")
    lines.extend(f"@include {inc}" for inc in table.includes)
    for entry in table.own_entries():
        lines.extend(format_entry_lines(entry))
    return "\n".join(lines) + "\n"


__all__ = [
    "KEY_FIELD",
    "KEY_HEAD",
    "Comments",
    "TableFile",
    "comment_lines",
    "comment_text",
    "format_entry",
    "format_entry_lines",
    "is_native",
    "parse_entry",
    "parse_effect",
    "parse_entry_lines",
    "parse_native",
    "parse_param",
    "write_native",
]
