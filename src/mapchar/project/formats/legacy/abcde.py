"""The abcde table dialect: ``docs/abcde/``.

The one legacy dialect mapchar writes as well as reads: an Atlas export needs
its tables spelled the way abcde's Atlas module reads them
(:mod:`mapchar.project.exchange.atlas`), so the writer sits here beside the
reader whose inverse it is.
"""

from __future__ import annotations

import re

from mapchar.core.bits import format_key
from mapchar.core.errors import TableError
from mapchar.core.notices import Level, Notice
from mapchar.core.table import RETURN, SwitchParam, Table, TableEntry, TokenKind
from mapchar.project.formats.legacy import legacy_text
from mapchar.project.formats.table_native import (
    KEY_FIELD,
    Comments,
    TableFile,
    parse_key,
    parse_stop,
    sanitize_id,
    sanitize_label,
)
from mapchar.project.formats.textfile import split_lines

_ABCDE_ENTRY = re.compile(r"^(?P<prefixes>(?:/|!){0,2})" + KEY_FIELD + r"(?P<rhs>.*)$")
_ABCDE_PARAM = re.compile(
    r"^(?:<@(?P<id>[^<>]+)>:|(?P<binary><binary>:))?(?P<match>-1|0|[1-9][0-9]*|\$(?:[0-9A-Fa-f]{2})+|%[01]+)(?:<(?P<w>-?\d+)>)?(?P<plus>\+?)$"
)


# --- reading --------------------------------------------------------------


def read_abcde(
    text: str, path: str | None = None, default_id: str = "table"
) -> TableFile:
    tables: list[Table] = []
    notices: list[Notice] = []
    current: Table | None = None
    current_named = False
    comments = Comments()

    def note(n: int, msg: str) -> None:
        notices.append(Notice(f"line {n}: {msg}", Level.INFO))

    for n, line in enumerate(split_lines(text), start=1):
        if line.startswith("#"):
            comments.add(line)
            continue
        if not line.strip():
            comments.flush()
            continue
        m = re.match(r"^@([^<>]+)$", line)
        if m:
            comments.flush()
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
        prefixes, key, weight, rhs = em.group("prefixes", "key", "weight", "rhs")
        if prefixes.count("/") > 1 or prefixes.count("!") > 1:
            raise TableError("repeated prefix", path, n)
        bits = parse_key(key)
        w = int(weight) if weight is not None else 1
        is_end = "/" in prefixes
        if "!" not in prefixes:
            kind = TokenKind.END if is_end else TokenKind.TEXT
            entry = TableEntry(bits, kind, legacy_text(rhs.replace("\\n", "\n")), w)
        else:
            entry = _abcde_switch(bits, w, rhs, is_end, n, path, note)
        if bits in current.entries:
            raise TableError(f"duplicate key {key!r}", path, n)
        current.add(comments.take(entry))
    comments.flush()
    if not tables:
        tables.append(Table(default_id))
    first, *extra = tables
    first.comment = comments.file_text()
    for t in extra:
        notices.append(
            Notice(f"table {t.id!r} split from the file into its own entry", Level.INFO)
        )
    return TableFile(first, notices, "abcde", extra)


def _abcde_switch(
    bits: str, weight: int, rhs: str, is_end: bool, n: int, path: str | None, note
) -> TableEntry:
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
                return TableEntry(bits, TokenKind.RETURN, "", weight)
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
    return TableEntry(
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


# --- writing --------------------------------------------------------------


def write_abcde_table(table: Table) -> str:
    """``table`` as an abcde table file, the inverse of :func:`read_abcde`."""
    lines = [f"@{table.id}"]
    for e in table.sorted_entries():
        key = format_key(e.bits)
        weight = f"<{e.weight}>" if e.weight != 1 else ""
        if e.kind is TokenKind.TEXT:
            lines.append(f"{key}{weight}={_abcde_text(e.text)}")
        elif e.kind is TokenKind.END:
            lines.append(f"/{key}{weight}={_abcde_text(e.text)}")
        elif e.kind is TokenKind.RETURN:
            lines.append(f"!{key}{weight}=,-1")
        elif e.kind is TokenKind.CODE:
            n = sum(o.bits for o in e.operands) // 8
            lines.append(f"!{key}{weight}=<[{e.text}]>,{n}")
        else:
            params = []
            for p in e.params:
                if p.table_id == "return":
                    params.append("-1")
                    continue
                m = p.stop.spec(any_marker="0")
                if p.table_id == "raw":
                    params.append(m + ("+" if p.shared else ""))
                elif p.table_id == "bits":
                    params.append(f"<binary>:{m}" + ("+" if p.shared else ""))
                else:
                    params.append(f"<@{p.table_id}>:{m}" + ("+" if p.shared else ""))
            label = f"<{_abcde_text(e.text)}>" if e.text else ""
            lines.append(f"!{key}{weight}={label}," + ",".join(params))
    lines.append("")
    return "\n".join(lines)


def _abcde_text(script_form: str) -> str:
    """Native text to abcde text: brackets literal, ``\\n`` kept, no other escapes."""
    out = []
    i = 0
    while i < len(script_form):
        c = script_form[i]
        if c == "\\" and i + 1 < len(script_form):
            nxt = script_form[i + 1]
            out.append("\\n" if nxt == "n" else nxt)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out).replace("<", "").replace(">", "")
