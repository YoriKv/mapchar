"""The native script: the text form of blocks and their strings.

See ``docs/plan/script-format.md``. ``#`` comments, ``@`` directives,
everything else content; line breaks inside a string are joined with nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    Source,
    StringRecord,
    StringType,
    WriteMode,
)
from mapchar.core.errors import ScriptError
from mapchar.core.numbers import format_num, parse_num
from mapchar.core.text import split_lines

HEADER = "@mapchar script 1"


class DumpMode(Enum):
    ORIGINALS = "originals"
    TRANSLATIONS = "translations"
    BOTH = "both"


@dataclass
class ScriptBlock:
    name: str
    config: BlockConfig | None
    strings: list[ScriptString] = field(default_factory=list)


@dataclass
class ScriptString:
    index: int
    start: int
    end: int
    pointers: tuple[int, ...]
    text: str
    original: str | None = None


@dataclass
class Script:
    rom: str | None = None
    tables: list[str] = field(default_factory=list)
    blocks: list[ScriptBlock] = field(default_factory=list)


# --- writing ---------------------------------------------------------------


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_config(config: BlockConfig) -> str:
    parts: list[str] = []
    s = config.source
    if isinstance(s, RangeSource):
        parts += [
            "source=range",
            f"start={format_num(s.start)}",
            f"stop={format_num(s.stop)}",
        ]
    elif isinstance(s, PointerTableSource):
        parts += [
            "source=pointers",
            f"start={format_num(s.start)}",
            f"stop={format_num(s.stop)}",
            f"size={s.size}",
            f"stride={s.stride}",
            f"endian={s.endian}",
            f"mapping={s.mapping_id}",
            f"offset={s.offset}",
            f"bank={s.bank}",
        ]
    elif isinstance(s, PointerListSource):
        parts += [
            "source=list",
            "addresses=" + ",".join(format_num(a) for a in s.addresses),
            f"size={s.size}",
            f"endian={s.endian}",
            f"mapping={s.mapping_id}",
            f"offset={s.offset}",
            f"bank={s.bank}",
        ]
    st = config.string_type
    if isinstance(st, EndToken):
        parts.append("type=end")
    elif isinstance(st, FixedLength):
        parts.append(f"type=fixed:{st.length}" + (":stop" if st.stop_at_end else ""))
    elif isinstance(st, Pascal):
        parts.append(
            f"type=pascal:{st.width}"
            + (":tokens" if st.counts_tokens else "")
            + (":big" if st.endian == "big" else "")
        )
    elif isinstance(st, NextPointer):
        parts.append("type=next")
    elif isinstance(st, Lines):
        parts.append(f"type=lines:{st.count}")
    parts.append(f"table={config.table_id}")
    if config.strings_per_pointer != 1:
        parts.append(f"spp={config.strings_per_pointer}")
    if config.realign[0]:
        parts.append(f"realign={config.realign[0]}:{config.realign[1]}")
    if config.skips:
        parts.append(
            "skips="
            + ",".join(f"{format_num(a)}>{format_num(b)}" for a, b in config.skips)
        )
    if config.line_length:
        parts.append(f"lines={config.line_length}")
    if config.show_end:
        parts.append(f"show_end={config.end_label}")
    if config.line_label != "line":
        parts.append(f"line_label={config.line_label}")
    if config.bound is not None:
        parts.append(f"bound={format_num(config.bound)}")
    if config.write_mode is not None:
        parts.append(f"mode={config.write_mode.value}")
    if config.fill != 0xFF:
        parts.append(f"fill={format_num(config.fill)}")
    return " ".join(parts)


def _content_lines(text: str) -> list[str]:
    lines = []
    for line in text.split("\n"):
        if line[:1] in ("@", "#", "\\"):
            line = "\\" + line
        lines.append(line)
    return lines


def write_script(
    blocks: list[tuple[str, BlockConfig, list[StringRecord]]],
    mode: DumpMode = DumpMode.TRANSLATIONS,
    rom: str | None = None,
    tables: list[str] = (),
) -> str:
    out = [HEADER]
    if rom:
        out.append(f"@rom {_quote(rom)}")
    for t in tables:
        out.append(f"@table {_quote(t)}")
    for name, config, strings in blocks:
        out.append("")
        out.append(f"@block {_quote(name)} {format_config(config)}")
        for rec in strings:
            ptrs = "".join(f" {format_num(p.address)}" for p in rec.pointers)
            ptr_part = f" ptr{ptrs}" if ptrs else ""
            span = f"{format_num(rec.start)}-{format_num(rec.end)}"
            out.append(f"@string {rec.index} at {span}{ptr_part}")
            if mode is DumpMode.ORIGINALS:
                out.extend(_content_lines(rec.original_text()))
                continue
            if mode is DumpMode.BOTH:
                out.extend("# " + line for line in rec.original_text().split("\n"))
            out.extend(_content_lines(rec.current_text()))
    return "\n".join(out) + "\n"


# --- reading ---------------------------------------------------------------

_STRING = re.compile(
    r"^@string\s+(?P<index>\d+)\s+at\s+(?P<start>\$[0-9A-Fa-f]+|\d+)-(?P<end>\$[0-9A-Fa-f]+|\d+)"
    r"(?:\s+ptr(?P<ptrs>(?:\s+(?:\$[0-9A-Fa-f]+|\d+))*))?\s*$"
)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def parse_config(spec: str) -> BlockConfig:
    fields: dict[str, str] = {}
    for word in spec.split():
        key, eq, value = word.partition("=")
        if not eq:
            raise ValueError(f"bad field {word!r}")
        fields[key] = value
    kind = fields.get("source", "range")
    source: Source
    if kind == "range":
        source = RangeSource(parse_num(fields["start"]), parse_num(fields["stop"]))
    elif kind == "pointers":
        source = PointerTableSource(
            parse_num(fields["start"]),
            parse_num(fields["stop"]),
            int(fields["size"]),
            int(fields.get("stride", fields["size"])),
            fields.get("endian", "little"),
            fields.get("mapping", "linear"),
            int(fields.get("offset", "0")),
            int(fields.get("bank", "0")),
        )
    elif kind == "list":
        source = PointerListSource(
            tuple(parse_num(a) for a in fields["addresses"].split(",") if a),
            int(fields["size"]),
            fields.get("endian", "little"),
            fields.get("mapping", "linear"),
            int(fields.get("offset", "0")),
            int(fields.get("bank", "0")),
        )
    else:
        raise ValueError(f"unknown source {kind!r}")
    type_spec = fields.get("type", "end").split(":")
    string_type: StringType
    if type_spec[0] == "end":
        string_type = EndToken()
    elif type_spec[0] == "fixed":
        string_type = FixedLength(int(type_spec[1]), "stop" in type_spec[2:])
    elif type_spec[0] == "pascal":
        string_type = Pascal(
            int(type_spec[1]),
            "tokens" in type_spec[2:],
            "big" if "big" in type_spec[2:] else "little",
        )
    elif type_spec[0] == "next":
        string_type = NextPointer()
    elif type_spec[0] == "lines":
        string_type = Lines(int(type_spec[1]))
    else:
        raise ValueError(f"unknown string type {type_spec[0]!r}")
    realign = (0, 0)
    if "realign" in fields:
        m, o = fields["realign"].split(":")
        realign = (int(m), int(o))
    skips = ()
    if fields.get("skips"):
        skips = tuple(
            (parse_num(a), parse_num(b))
            for a, b in (pair.split(">") for pair in fields["skips"].split(","))
        )
    return BlockConfig(
        source=source,
        string_type=string_type,
        table_id=fields.get("table", ""),
        strings_per_pointer=int(fields.get("spp", "1")),
        realign=realign,
        skips=skips,
        line_length=int(fields.get("lines", "0")),
        bound=parse_num(fields["bound"]) if "bound" in fields else None,
        write_mode=WriteMode(fields["mode"]) if "mode" in fields else None,
        fill=parse_num(fields["fill"]) if "fill" in fields else 0xFF,
        show_end="show_end" in fields,
        end_label=fields.get("show_end", "end"),
        line_label=fields.get("line_label", "line"),
    )


def parse_script(text: str, path: str | None = None) -> Script:
    script = Script()
    block: ScriptBlock | None = None
    current: ScriptString | None = None
    content: list[str] = []
    originals: list[str] = []
    seen_header = False

    def flush() -> None:
        nonlocal current
        if current is not None:
            current.text = "".join(content)
            if originals:
                current.original = "".join(originals)
            current = None
        content.clear()
        originals.clear()

    for n, raw in enumerate(split_lines(text), start=1):
        line = raw
        if not seen_header:
            if not line.strip():
                continue
            if line.strip() != HEADER:
                raise ScriptError(f"expected {HEADER!r}", path, n)
            seen_header = True
            continue
        if line.startswith("#"):
            if current is not None:
                originals.append(line[2:] if line.startswith("# ") else line[1:])
            continue
        if line.startswith("@"):
            flush()
            head, _, rest = line.partition(" ")
            if head == "@rom":
                m = _QUOTED.match(rest.strip())
                if not m:
                    raise ScriptError("@rom needs a quoted path", path, n)
                script.rom = _unquote(m.group(1))
            elif head == "@table":
                m = _QUOTED.match(rest.strip())
                if not m:
                    raise ScriptError("@table needs a quoted path", path, n)
                script.tables.append(_unquote(m.group(1)))
            elif head == "@block":
                m = _QUOTED.match(rest.strip())
                if not m:
                    raise ScriptError("@block needs a quoted name", path, n)
                spec = rest.strip()[m.end() :].strip()
                try:
                    config = parse_config(spec) if spec else None
                except (ValueError, KeyError) as exc:
                    raise ScriptError(
                        f"bad block configuration: {exc}", path, n
                    ) from None
                block = ScriptBlock(_unquote(m.group(1)), config)
                script.blocks.append(block)
            elif head == "@string":
                sm = _STRING.match(line)
                if not sm or block is None:
                    raise ScriptError("bad @string line", path, n)
                ptrs = tuple(parse_num(w) for w in (sm.group("ptrs") or "").split())
                current = ScriptString(
                    int(sm.group("index")),
                    parse_num(sm.group("start")),
                    parse_num(sm.group("end")),
                    ptrs,
                    "",
                )
                block.strings.append(current)
            else:
                raise ScriptError(f"unknown directive {head}", path, n)
            continue
        if current is None:
            if line.strip():
                raise ScriptError("text outside a @string", path, n)
            continue
        if line[:1] == "\\" and line[1:2] in ("@", "#", "\\"):
            line = line[1:]
        content.append(line)
    flush()
    if not seen_header:
        raise ScriptError(f"missing {HEADER!r}", path)
    return script
