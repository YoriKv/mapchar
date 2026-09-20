"""The native script: the text form of blocks and their strings.

See ``docs/plan/script-format.md``. ``#`` comments, ``@`` directives,
everything else content; line breaks inside a string are joined with nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from mapchar.core.block import BlockConfig, StringRecord
from mapchar.core.errors import ScriptError
from mapchar.core.numbers import NUM, format_num, parse_num
from mapchar.project.formats.blockspec import format_config, parse_config
from mapchar.project.formats.textfile import quoted, split_lines, unescape

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


@dataclass
class Script:
    rom: str | None = None
    tables: list[str] = field(default_factory=list)
    blocks: list[ScriptBlock] = field(default_factory=list)


# --- writing ---------------------------------------------------------------


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
        out.append(f"@rom {quoted(rom)}")
    for t in tables:
        out.append(f"@table {quoted(t)}")
    for name, config, strings in blocks:
        out.append("")
        out.append(f"@block {quoted(name)} {format_config(config)}")
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
    rf"^@string\s+(?P<index>\d+)\s+at\s+(?P<start>{NUM})-(?P<end>{NUM})"
    rf"(?:\s+ptr(?P<ptrs>(?:\s+(?:{NUM}))*))?\s*$"
)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def parse_script(text: str, path: str | None = None) -> Script:
    script = Script()
    block: ScriptBlock | None = None
    current: ScriptString | None = None
    content: list[str] = []
    seen_header = False

    def flush() -> None:
        nonlocal current
        if current is not None:
            current.text = "".join(content)
            current = None
        content.clear()

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
            continue
        if line.startswith("@"):
            flush()
            head, _, rest = line.partition(" ")
            if head == "@rom":
                m = _QUOTED.match(rest.strip())
                if not m:
                    raise ScriptError("@rom needs a quoted path", path, n)
                script.rom = unescape(m.group(1))
            elif head == "@table":
                m = _QUOTED.match(rest.strip())
                if not m:
                    raise ScriptError("@table needs a quoted path", path, n)
                script.tables.append(unescape(m.group(1)))
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
                block = ScriptBlock(unescape(m.group(1)), config)
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


# --- applying --------------------------------------------------------------


@dataclass
class ScriptImportReport:
    applied: int = 0
    notices: list[str] = field(default_factory=list)
    new_blocks: list[tuple[str, BlockConfig]] = field(default_factory=list)
    """Blocks the script carries that the project lacks, with their config."""
    texts: dict[str, dict[int, str]] = field(default_factory=dict)
    """Per block, the text each placed string is to hold, by index."""


def apply_script(
    script: Script, blocks: dict[str, list[StringRecord]]
) -> ScriptImportReport:
    """Walk ``script`` over the project's strings, block by block and index by
    index: what each placed string is to say comes back as ``texts``, and what
    could not be placed as notices. Nothing is changed here — the texts go
    into the bytes, which is the window's to do."""
    report = ScriptImportReport()
    for sb in script.blocks:
        strings = blocks.get(sb.name)
        if strings is None:
            if sb.config is not None:
                report.new_blocks.append((sb.name, sb.config))
            else:
                report.notices.append(
                    f"{sb.name}: not in the project and no configuration"
                )
            continue
        by_index = {s.index: s for s in strings}
        for ss in sb.strings:
            rec = by_index.get(ss.index)
            if rec is None:
                report.notices.append(f"{sb.name}/{ss.index}: no such string")
                continue
            if (ss.start, ss.end) != (rec.start, rec.end):
                report.notices.append(
                    f"{sb.name}/{ss.index}: script says ${ss.start:X}-${ss.end:X}, "
                    f"project has ${rec.start:X}-${rec.end:X}"
                )
            report.texts.setdefault(sb.name, {})[ss.index] = ss.text
            report.applied += 1
    return report
