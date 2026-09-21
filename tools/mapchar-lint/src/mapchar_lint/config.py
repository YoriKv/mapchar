"""A block's configuration line, read the way ``parse_config`` reads it.

``mapchar.project.formats.blockspec.parse_config`` splits the line into
``key=value`` words and keeps the last of each key. It is strict about what it
reads — a word with no ``=``, a number that does not parse, a source or string
type it does not know all raise, and a block whose line raises is **dropped from
the project** — and blind to everything else: a word it does not read is passed
over, so ``tabel=main`` loses the table without a sound.

:func:`read_config` does the same reading and reports both halves: what would
raise, and what would be ignored or misread. It hands back the numbers it read,
so the cross-reference and file-size checks need not parse the line again.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from mapchar_lint.schema import (
    CONFIG_KEYS,
    ENDIANS,
    MAX_HEADER,
    REQUIRED_SOURCE_KEYS,
    SOURCE_KEYS,
    SOURCES,
    STRING_TYPES,
    WRITE_MODES,
)


@dataclass(frozen=True)
class Finding:
    code: str
    #: ``"error"``, ``"warning"`` or ``"info"``.
    severity: str
    message: str
    detail: str = ""


@dataclass
class ConfigReading:
    """What one configuration line says, and what is wrong with it."""

    findings: list = field(default_factory=list)
    #: Set when ``parse_config`` would raise on the line.
    fatal: bool = False
    source: str | None = None
    string_type: str | None = None
    table: str = ""
    mapping: str | None = None
    size: int | None = None
    #: Every address the line names, by the word it came from — ``start``,
    #: ``stop``, ``bound``, each of ``addresses`` and each skip end — for the
    #: file-size check.
    addresses: list = field(default_factory=list)

    def add(self, code: str, severity: str, message: str, detail: str = "") -> None:
        self.findings.append(Finding(code, severity, message, detail))

    def drop(self, code: str, message: str) -> None:
        """A problem ``parse_config`` raises on."""
        self.fatal = True
        self.add(code, "error", message)


def parse_num(text: str) -> int:
    """``$hex``, ``-$hex``, ``$-hex`` or decimal — ``mapchar.core.numbers``."""
    text = text.strip()
    if text.startswith(("$-", "-$")):
        return -int(text[2:], 16)
    if text.startswith("$"):
        return int(text[1:], 16)
    return int(text, 10)


def read_config(spec: object) -> ConfigReading:
    """Read ``spec`` as ``parse_config`` would, collecting findings."""
    out = ConfigReading()
    if not isinstance(spec, str):
        out.drop(
            "E602", f"the configuration is a JSON {type(spec).__name__}, not a line"
        )
        return out
    fields: dict[str, str] = {}
    for word in spec.split():
        key, eq, value = word.partition("=")
        if not eq:
            out.drop("E603", f"{word!r} is not a key=value word")
            return out
        if key in fields and fields[key] != value:
            out.add(
                "W612",
                "warning",
                f"{key} is given twice; {key}={value} wins over {key}={fields[key]}",
                "The line keeps the last of each key.",
            )
        fields[key] = value
    source = fields.get("source", "range")
    if source not in SOURCES:
        out.drop("E604", f"source={source} is not a source ({', '.join(SOURCES)})")
        return out
    out.source = source
    for key in REQUIRED_SOURCE_KEYS[source]:
        if key not in fields:
            out.drop("E605", f"a {source} source needs {key}=")
    if out.fatal:
        return out
    _unread(out, fields, source)
    _numbers(out, fields, source)
    if out.fatal:
        return out
    _string_type(out, fields)
    _writing(out, fields)
    if out.fatal:
        return out
    _pointers(out, fields, source)
    table = fields.get("table", "")
    out.table = table
    if not table:
        out.add(
            "E620",
            "error",
            "no table=",
            "A block reads its bytes through a table; with none it reads nothing.",
        )
    return out


def _unread(out: ConfigReading, fields: dict, source: str) -> None:
    """Words ``parse_config`` passes over: not settings at all, or settings
    this source does not take."""
    other_sources = {k for s, keys in SOURCE_KEYS.items() if s != source for k in keys}
    for key, value in fields.items():
        if key not in CONFIG_KEYS:
            close = difflib.get_close_matches(key, sorted(CONFIG_KEYS), n=1)
            hint = f" — did you mean {close[0]}=?" if close else ""
            out.add(
                "E610",
                "error",
                f"{key}={value} is not a setting{hint}",
                "The line passes over it, so what it says is not in the block, and "
                "the next save drops it.",
            )
        elif key in other_sources and key not in SOURCE_KEYS[source]:
            out.add(
                "W611",
                "warning",
                f"{key}={value} is not read by a {source} source",
                "Ignored, and dropped by the next save.",
            )


def _numbers(out: ConfigReading, fields: dict, source: str) -> None:
    """Every number the line holds, parsed the way the reader parses it."""
    taken = SOURCE_KEYS[source]
    for key in ("start", "stop", "null", "inner_null", "bound"):
        if key in fields and (key in taken or key == "bound") and fields[key]:
            try:
                value = parse_num(fields[key])
            except ValueError:
                out.drop("E606", f"{key}={fields[key]} is not a number")
                continue
            if key in ("start", "stop", "bound"):
                out.addresses.append((key, value))
    for key in (
        "size",
        "stride",
        "offset",
        "bank",
        "inner_size",
        "spp",
        "lines",
        "header",
    ):
        if key in fields and (key in taken or key in ("spp", "lines", "header")):
            try:
                number = int(_run_count(fields[key]) if key == "spp" else fields[key])
            except ValueError:
                out.drop("E606", f"{key}={fields[key]} is not a decimal number")
                continue
            if key == "header" and not 0 <= number <= MAX_HEADER:
                out.drop("E626", f"header={number} is not 0 to {MAX_HEADER}")
    if source == "list":
        for item in fields["addresses"].split(","):
            if not item:
                continue
            try:
                out.addresses.append(("addresses", parse_num(item)))
            except ValueError:
                out.drop("E606", f"addresses holds {item!r}, which is not a number")
    if "realign" in fields:
        m, colon, o = fields["realign"].partition(":")
        try:
            if not colon:
                raise ValueError
            int(m), int(o)
        except ValueError:
            out.drop("E606", f"realign={fields['realign']} is not multiple:offset")
    if fields.get("skips"):
        for pair in fields["skips"].split(","):
            a, gt, b = pair.partition(">")
            try:
                if not gt:
                    raise ValueError
                start, stop = parse_num(a), parse_num(b)
            except ValueError:
                out.drop("E606", f"skips holds {pair!r}, which is not from>to")
                continue
            out.addresses += [("skips", start), ("skips", stop)]
            if start == stop:
                out.add("W622", "warning", f"the skip {pair} lands where it starts")


def _string_type(out: ConfigReading, fields: dict) -> None:
    spec = fields.get("type", "end").split(":")
    kind = spec[0]
    if kind not in STRING_TYPES:
        out.drop("E607", f"type={fields['type']} is not a string type")
        return
    out.string_type = kind
    if kind in ("fixed", "pascal", "lines"):
        try:
            number = int(spec[1])
        except (IndexError, ValueError):
            out.drop("E607", f"type={fields.get('type')} needs a number: {kind}:N")
            return
        if number < 1:
            out.add(
                "W623",
                "warning",
                f"type={fields['type']} counts {number}",
                "No string can be read at that length.",
            )
        flags = set(spec[2:])
        allowed = {"fixed": {"stop"}, "pascal": {"tokens", "big"}, "lines": set()}
        for flag in sorted(flags - allowed[kind]):
            out.add(
                "E624",
                "error",
                f"type={fields['type']}: {flag!r} is not a {kind} flag",
                "The reader looks for its own flags and passes this one over.",
            )
    if kind == "next" and out.source == "range":
        out.add(
            "W625",
            "warning",
            "type=next needs a pointer source",
            "A range has no next pointer; its strings read to end tokens.",
        )


def _writing(out: ConfigReading, fields: dict) -> None:
    mode = fields.get("mode")
    if mode is not None and mode not in WRITE_MODES:
        out.drop("E608", f"mode={mode} is not a write mode ({', '.join(WRITE_MODES)})")
    if mode == "packed":
        breaks = []
        if fields.get("skips"):
            breaks.append("skip ranges")
        if out.source == "range" and int(fields.get("header", "0")):
            breaks.append("a record header")
        if breaks:
            out.add(
                "W627",
                "warning",
                f"mode=packed on a block with {' and '.join(breaks)}",
                "The block is written slotted: packing would lay the strings "
                "over the bytes they step around.",
            )
    fill = fields.get("fill")
    if fill is not None:
        text = fill.strip()
        try:
            if text.startswith("$"):
                digits = text[1:]
                if not digits:
                    raise ValueError
                bytes.fromhex(digits.zfill(len(digits) + len(digits) % 2))
            elif not 0 <= int(text, 10) <= 0xFF:
                raise ValueError
        except ValueError:
            out.drop("E609", f"fill={fill} is not $hex bytes or a decimal byte")
    spp = fields.get("spp")
    if spp is not None and int(_run_count(spp)) < 1:
        out.add("W621", "warning", f"spp={spp} reads as 1")


def _run_count(spp: str) -> str:
    """The count in ``spp=N``, ``spp=next`` or ``spp=next:N``."""
    head, _, last = spp.partition(":")
    return (last or "1") if head == "next" else spp


def _pointers(out: ConfigReading, fields: dict, source: str) -> None:
    if source == "range":
        start = parse_num(fields["start"])
        stop = parse_num(fields["stop"])
        if start >= stop:
            out.add(
                "W619", "warning", f"start={fields['start']} is not before stop", ""
            )
        return
    out.mapping = fields.get("mapping", "linear")
    size = int(fields["size"])
    out.size = size
    if size < 1:
        out.add("E617", "error", f"size={size}: a pointer takes at least one byte")
    for key in ("endian", "inner_endian"):
        value = fields.get(key)
        if value is not None and key in SOURCE_KEYS[source] and value not in ENDIANS:
            out.add(
                "E613",
                "error",
                f"{key}={value} is not little or big",
                "Anything but `big` reads as little-endian.",
            )
    if source in ("pointers", "nested"):
        stride = int(
            fields.get("stride", fields["size"] if source == "pointers" else 0)
        )
        if "stride" in fields:
            if stride < 1:
                out.add("E618", "error", f"stride={stride}: pointers must advance")
            elif stride < size:
                out.add(
                    "W618",
                    "warning",
                    f"stride={stride} is less than size={size}",
                    "Each pointer overlaps the next.",
                )
        start, stop = parse_num(fields["start"]), parse_num(fields["stop"])
        if start >= stop:
            out.add(
                "W619",
                "warning",
                f"start={fields['start']} is not before stop={fields['stop']}",
                "The table holds no pointers, so the block reads no strings.",
            )
    if source == "list" and not any(k == "addresses" for k, _ in out.addresses):
        out.add("W619", "warning", "addresses= lists none", "The block reads nothing.")
