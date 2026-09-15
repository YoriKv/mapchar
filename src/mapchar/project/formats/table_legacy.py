"""Readers for the romjuice, Cartographer, Atlas and abcde table dialects.

Each follows its own tool's parsing rules (``docs/table-dialects.md`` and the
tool docs) and converts to the native model, recording what the conversion
changed as notices. Nothing here writes a legacy dialect.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from mapchar.core.bits import hex_to_bits
from mapchar.core.errors import TableError
from mapchar.core.notices import Level, Notice
from mapchar.core.table import (
    RETURN,
    Entry,
    OperandSpec,
    Stop,
    SwitchParam,
    Table,
    TokenKind,
    parse_stop,
    sanitize_id,
    sanitize_label,
)
from mapchar.project.formats.table_native import (
    TableFile,
    is_native,
    parse_native,
    split_lines,
)

DIALECTS = ("native", "abcde", "cartographer", "atlas", "romjuice")


def detect_dialect(text: str) -> tuple[str, bool]:
    """Guess the dialect; the flag says whether the guess is certain."""
    if is_native(text):
        return "native", True
    lines = [ln.rstrip() for ln in split_lines(text)]
    for line in lines:
        if (
            re.match(r"^@[^<>=]+$", line)
            or re.match(r"^/?!/?[0-9A-Fa-f%]+(<-?\d+>)?=", line)
            and "," in line
        ):
            return "abcde", True
        if line.startswith("%") or line.startswith("#"):
            return "abcde", True
    for line in lines:
        if re.match(r"^![0-9A-Fa-f]+$", line) or re.match(r"^@[0-9A-Fa-f]+=\S+,", line):
            return "romjuice", True
        if re.match(r"^\$[0-9A-Fa-f]+=\d+$", line):
            return "romjuice", True
    for line in lines:
        if re.match(r"^\$[0-9A-Fa-f]+=[^,]+,\d+$", line):
            return "cartographer", True
    for line in lines:
        if re.match(r"^\*[0-9A-Fa-f]+", line) or re.match(r"^/[^0-9A-Fa-f]", line):
            return "atlas", True
    return "cartographer", False


def load_table_text(
    text: str, path: str | None = None, dialect: str | None = None, **options
) -> TableFile:
    """Load a table file in the given dialect, or the detected one."""
    if dialect is None:
        dialect, _ = detect_dialect(text)
    default_id = table_id_for(path)
    if dialect == "native":
        return parse_native(text, path, default_id)
    readers: dict[str, Callable[..., TableFile]] = {
        "abcde": read_abcde,
        "cartographer": read_cartographer,
        "atlas": read_atlas,
        "romjuice": read_romjuice,
    }
    if dialect not in readers:
        raise TableError(f"unknown dialect {dialect!r}", path)
    result = readers[dialect](text, path, default_id, **options)
    result.dialect = dialect
    return result


def table_id_for(path: str | None) -> str:
    """The name a table file without a ``@table`` line gives its table."""
    if not path:
        return "table"
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return sanitize_id(name) or "table"


def free_table_id(table_id: str, taken) -> str:
    """``table_id``, numbered up (``main_2``) until it is not in ``taken``."""
    if table_id not in taken:
        return table_id
    n = 2
    while f"{table_id}_{n}" in taken:
        n += 1
    return f"{table_id}_{n}"


_CODE_IN_TEXT = re.compile(r"\[([^\[\]]+)\]")


def legacy_text(raw: str) -> str:
    """Native script form of legacy text whose line breaks are real characters.

    Bracketed code notation such as ``[END]`` or ``[cardinal #]`` is kept as
    a code (whitespace inside becoming ``_``), inside text too; any other
    bracket is escaped so it stays literal text.
    """
    body = raw.replace("\\", "\\\\")
    body = body.replace("\n", "\\n")
    out = []
    pos = 0
    for m in _CODE_IN_TEXT.finditer(body):
        inner = m.group(1)
        if inner[0] in "$%" or "\\" in inner:
            continue
        out.append(body[pos : m.start()].replace("[", "\\[").replace("]", "\\]"))
        out.append(f"[{sanitize_label(inner)}]")
        pos = m.end()
    out.append(body[pos:].replace("[", "\\[").replace("]", "\\]"))
    return "".join(out)


# --- romjuice -------------------------------------------------------------

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
    text: str,
    path: str | None = None,
    default_id: str = "table",
    swap_table: str | None = None,
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
            key = _rj_key(line[1:])
            if key is None:
                continue
            bits, _ = key
            if swap_table is None:
                note(n, "swap entry dropped: no second table given")
                continue
            entry = Entry(
                bits, TokenKind.SWITCH, "[swap]", params=(SwitchParam(swap_table),)
            )
            pending.append((n, entry))
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
        if entry.bits in table.entries:
            note(n, f"duplicate key {entry.bits!r}: first entry wins")
            continue
        try:
            table.add(entry)
        except TableError as exc:
            note(n, f"{exc.message}; entry dropped")

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


# --- Cartographer PR3 -----------------------------------------------------

_HEXKEY = re.compile(r"^[0-9A-Fa-f]+$")


def _cart_text(value: str) -> str:
    return legacy_text(value.replace("\\n", "\n").replace("\\r", "\n"))


def read_cartographer(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    table = Table(default_id)
    notices: list[Notice] = []
    for n, line in enumerate(split_lines(text), start=1):
        if not line.strip():
            continue
        first = line[0]
        if first == "$":
            m = re.match(r"^\$([0-9A-Fa-f]+)=(.*),(\d+)\s*$", line)
            if not m:
                raise TableError("bad linked entry", path, n)
            key, label, count = m.groups()
            bits = _even_key(key, path, n)
            entry = Entry(
                bits,
                TokenKind.CODE,
                sanitize_label(label),
                operands=(OperandSpec("bytes", int(count) * 8),),
            )
        elif first == "/":
            key, _, value = line[1:].partition("=")
            bits = _even_key(key, path, n)
            entry = Entry(bits, TokenKind.END, _cart_text(value))
        elif _HEXKEY.match(first):
            key, eq, value = line.partition("=")
            if not eq:
                raise TableError("entry without '='", path, n)
            bits = _even_key(key, path, n)
            entry = Entry(bits, TokenKind.TEXT, _cart_text(value))
        else:
            raise TableError("line must start with a hex digit, '/' or '$'", path, n)
        _add_or_note(table, entry, n, notices)
    return TableFile(table, notices, "cartographer")


def _even_key(key: str, path: str | None, n: int) -> str:
    if not _HEXKEY.match(key) or len(key) % 2:
        raise TableError(f"key {key!r} must be whole bytes", path, n)
    return hex_to_bits(key)


def _add_or_note(table: Table, entry: Entry, n: int, notices: list[Notice]) -> None:
    if entry.bits in table.entries:
        notices.append(Notice(f"line {n}: duplicate key, first entry wins", Level.INFO))
        return
    try:
        table.add(entry)
    except TableError as exc:
        notices.append(Notice(f"line {n}: {exc.message}; entry dropped", Level.INFO))


# --- Atlas 1.11 -------------------------------------------------------------


def read_atlas(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    table = Table(default_id)
    notices: list[Notice] = []
    end_marker: str | None = None
    for n, line in enumerate(split_lines(text), start=1):
        if not line.strip() or line.startswith("//"):
            continue
        first = line[0]
        if first in "([{":
            continue
        if first == "$":
            notices.append(
                Notice(f"line {n}: '$' entry skipped, as Atlas does", Level.INFO)
            )
            continue
        if first in "!@":
            notices.append(Notice(f"line {n}: dakuten entry dropped", Level.INFO))
            continue
        if first == "*":
            key, eq, value = line[1:].partition("=")
            bits = _even_key(key, path, n)
            _add_or_note(
                table,
                Entry(bits, TokenKind.TEXT, legacy_text(value + "\n")),
                n,
                notices,
            )
            continue
        if first == "/":
            key, eq, value = line[1:].partition("=")
            if not eq:
                end_marker = legacy_text(key)
                notices.append(
                    Notice(
                        f"line {n}: hexless end marker {key!r} recorded as the"
                        " block's artificial end label",
                        Level.INFO,
                    )
                )
                continue
            bits = _even_key(key, path, n)
            _add_or_note(
                table, Entry(bits, TokenKind.END, legacy_text(value)), n, notices
            )
            continue
        key, eq, value = line.partition("=")
        if not eq or not _HEXKEY.match(key):
            raise TableError("not a table entry", path, n)
        bits = _even_key(key, path, n)
        _add_or_note(table, Entry(bits, TokenKind.TEXT, legacy_text(value)), n, notices)
    result = TableFile(table, notices, "atlas")
    result.end_marker = end_marker
    return result


# --- abcde ----------------------------------------------------------------

_ABCDE_ENTRY = re.compile(
    r"^(?P<prefixes>(?:/|!){0,2})(?P<lhs>%[01]+|[0-9A-Fa-f]+)(?:<(?P<weight>-?\d+)>)?=(?P<rhs>.*)$"
)
_ABCDE_PARAM = re.compile(
    r"^(?:<@(?P<id>[^<>]+)>:|(?P<binary><binary>:))?(?P<match>-1|0|[1-9][0-9]*|\$(?:[0-9A-Fa-f]{2})+|%[01]+)(?:<(?P<w>-?\d+)>)?(?P<plus>\+?)$"
)


def read_abcde(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    tables: list[Table] = []
    notices: list[Notice] = []
    current: Table | None = None
    current_named = False

    def note(n: int, msg: str) -> None:
        notices.append(Notice(f"line {n}: {msg}", Level.INFO))

    for n, line in enumerate(split_lines(text), start=1):
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^@([^<>]+)$", line)
        if m:
            tid = sanitize_id(m.group(1))
            if tid != m.group(1):
                note(n, f"table id {m.group(1)!r} renamed to {tid!r}")
            if current is not None and not current_named:
                current.id = tid
                current_named = True
            else:
                current = Table(tid)
                current_named = True
                tables.append(current)
            continue
        em = _ABCDE_ENTRY.match(line)
        if not em:
            raise TableError("not an abcde table line", path, n)
        if current is None:
            current = Table(default_id)
            tables.append(current)
        prefixes, lhs, weight, rhs = em.group("prefixes", "lhs", "weight", "rhs")
        if prefixes.count("/") > 1 or prefixes.count("!") > 1:
            raise TableError("repeated prefix", path, n)
        bits = lhs[1:] if lhs.startswith("%") else hex_to_bits(lhs)
        w = int(weight) if weight is not None else 1
        is_end = "/" in prefixes
        if "!" not in prefixes:
            kind = TokenKind.END if is_end else TokenKind.TEXT
            entry = Entry(bits, kind, legacy_text(rhs.replace("\\n", "\n")), w)
        else:
            entry = _abcde_switch(bits, w, rhs, is_end, n, path, note)
        if bits in current.entries:
            raise TableError(f"duplicate key {lhs!r}", path, n)
        current.add(entry)
    if not tables:
        tables.append(Table(default_id))
    first, *extra = tables
    for t in extra:
        notices.append(
            Notice(f"table {t.id!r} split from the file into its own entry", Level.INFO)
        )
    return TableFile(first, notices, "abcde", extra)


def _abcde_switch(
    bits: str, weight: int, rhs: str, is_end: bool, n: int, path: str | None, note
) -> Entry:
    label = ""
    rest = rhs
    if rhs.startswith("<"):
        close = rhs.find(">")
        if close < 0:
            raise TableError("unclosed switch label", path, n)
        label = rhs[1:close]
        rest = rhs[close + 1 :]
    if label.startswith("$"):
        raise TableError("switch label cannot start with '$'", path, n)
    if not rest.startswith(","):
        raise TableError("switch entry needs ',param'", path, n)
    raw_params = rest[1:].split(",")
    params: list[SwitchParam] = []
    for i, word in enumerate(raw_params):
        pm = _ABCDE_PARAM.match(word)
        if not pm:
            raise TableError(f"bad switch parameter {word!r}", path, n)
        tid, binary, match, w, plus = pm.group("id", "binary", "match", "w", "plus")
        if match == "-1":
            if tid is not None or i != len(raw_params) - 1:
                raise TableError(
                    "'-1' must be the last parameter and cannot name a table",
                    path,
                    n,
                )
            if not label and not params:
                return Entry(bits, TokenKind.RETURN, "", weight)
            params.append(SwitchParam(RETURN))
            break
        target = tid if tid is not None else ("bits" if binary else "raw")
        if tid is not None:
            target = sanitize_id(tid)
        stop = parse_stop(match)
        if w is not None and int(w) != 1:
            note(n, f"weight <{w}> on fallback bits dropped")
        params.append(SwitchParam(target, stop, bool(plus)))
    if is_end:
        note(n, "'/' on a switch entry dropped: the string does not end on return")
    return Entry(
        bits, TokenKind.SWITCH, _switch_text(label), weight, params=tuple(params)
    )


def _switch_text(label: str) -> str:
    """A switch's text from an abcde label.

    ``[X Y]`` becomes the code ``[X_Y]``; anything else is printed text,
    possibly empty (a silent switch).
    """
    if len(label) >= 2 and label[0] == "[" and label[-1] == "]" and "\n" not in label:
        return f"[{sanitize_label(label)}]"
    return legacy_text(label.replace("\\n", "\n"))
