"""Atlas scripts: export for abcde's Atlas module, and a subset importer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from mapchar.core.block import (
    BlockConfig,
    NestedPointerSource,
    Pascal,
    StringRecord,
    WriteMode,
)
from mapchar.core.numbers import format_num, parse_num
from mapchar.core.table import Table, TableSet, TokenKind
from mapchar.core.tokens import (
    CodeRef,
    TextRun,
    bits_for,
    escape_text,
    operand_values,
    parse_text,
    render,
)
from mapchar.pipeline.extract import is_hidden_end, strip_artificial
from mapchar.project.formats.legacy.abcde import write_abcde_table
from mapchar.project.formats.textfile import split_lines

ADDRESS_TYPES = {
    "linear": "LINEAR",
    "lorom": "LOROM00",
    "hirom": "HIROM",
    "gb": "GB",
    "relative": "POINTER_RELATIVE",
}


@dataclass
class AtlasExport:
    script: str
    tables: dict[str, str]
    """Table file name to its abcde-dialect text."""
    notices: list[str] = field(default_factory=list)


# --- script text --------------------------------------------------------------


def atlas_text(text: str, tables: TableSet | None) -> str:
    """Script text for Atlas: raw bytes as ``<$XX>``, operands as raw bytes."""
    out = []
    for item in parse_text(text):
        if isinstance(item, TextRun):
            out.append(escape_text(item.text).replace("\\[", "[").replace("\\]", "]"))
            continue
        ref: CodeRef = item
        if ref.is_raw_byte:
            out.append(f"<${ref.label[1:].upper()}>")
        elif ref.is_raw_bits:
            out.append("".join(f"<%{b}>" for b in ref.label[1:]))
        elif ref.words and tables is not None:
            found = tables.entry_for_label(ref.label)
            entry = found[0][1] if found else None
            if entry is None or entry.kind is not TokenKind.CODE:
                out.append(f"[{ref.label} {' '.join(ref.words)}]")
                continue
            values = operand_values(entry, ref.words)
            bits = bits_for(entry, values)[len(entry.bits) :]
            out.append(f"[{ref.label}]")
            for i in range(0, len(bits) - len(bits) % 8, 8):
                out.append(f"<${int(bits[i : i + 8], 2):02X}>")
        else:
            out.append(f"[{ref.label}]")
    return "".join(out)


def write_atlas(
    block_name: str,
    config: BlockConfig,
    strings: list[StringRecord],
    tables: TableSet,
    table_files: dict[str, Table],
    *,
    header: int = 0,
) -> AtlasExport:
    """An Atlas script plus abcde-dialect tables that insert the block.

    Atlas writes to the ROM **file**, so every address the script carries is a
    file offset: ``header`` — the container's header, what the block's own
    offsets drop — is added to each one. ``config`` is expected already shifted
    by the same amount
    (:func:`~mapchar.project.exchange.addresses.shift_config`), which is what
    moves the bound, the skips and ``#HDR``.
    """
    notices: list[str] = []
    out = [f"// mapchar: {block_name}"]
    files = {name: write_abcde_table(table) for name, table in table_files.items()}
    var_by_id: dict[str, str] = {}
    for n, (name, table) in enumerate(table_files.items()):
        var = f"Table_{n}"
        out.append(f"#VAR({var}, TABLE)")
        out.append(f'#ADDTBL("{name}", {var})')
        var_by_id[table.id] = var
    start_var = var_by_id.get(tables.start.id)
    if start_var:
        out.append(f"#ACTIVETBL({start_var})")
    else:
        out.append(f"#ACTIVETBL(@{tables.start.id})")
    src = config.source
    mapping = getattr(src, "mapping_id", "linear")
    nested = isinstance(src, NestedPointerSource)
    if nested:
        # Atlas has one header for every pointer; a nested source's inner
        # pointers each count from their own group's base.
        notices.append(
            "nested pointer tables have no Atlas form; every string is written "
            "in place and its pointers are left as they are"
        )
        mapping = ""
    elif config.has_pointers:
        if mapping not in ADDRESS_TYPES:
            notices.append(
                f"mapping {mapping!r} has no Atlas address type; pointers omitted"
            )
        else:
            out.append(f'#SMA("{ADDRESS_TYPES[mapping]}")')
        if getattr(src, "endian", "little") == "big":
            out.append('#ENDIANSWAP("TRUE")')
        offset = getattr(src, "offset", 0)
        if offset:
            out.append(f"#HDR({format_num(offset)})")
    st = config.string_type
    if isinstance(st, Pascal):
        out.append('#STRTYPE("PASCAL")')
        out.append(f"#PASCALLEN({st.width})")
        if st.counts_tokens:
            out.append('#PASCALTYPE("TOKENS")')
    fixed_len = config.fixed_length
    if fixed_len is not None:
        out.append(f"#FIXEDLENGTH({fixed_len}, {format_num(config.fill[0])})")
        if len(config.fill) > 1:
            notices.append(
                f"a fill pattern of {len(config.fill)} bytes has no Atlas form; "
                "padded with its first byte"
            )
    if config.realign[0]:
        out.append(f"#STRINGALIGN({config.realign[0]})")
        if config.realign[1]:
            notices.append("realign offset has no Atlas form")
    mode = config.effective_write_mode
    packed = mode is WriteMode.PACKED and not nested
    if packed and strings:
        bound = config.bound if config.bound is not None else getattr(src, "stop", None)
        start = strings[0].start + header
        if bound is not None:
            out.append(f"#JMP({format_num(start)}, {format_num(bound - 1)})")
        else:
            out.append(f"#JMP({format_num(start)})")
    width = {1: "W8", 2: "W16", 3: "W24", 4: "W32"}
    for rec in strings:
        out.append("")
        out.append(f"// #{rec.index}")
        if not packed:
            out.append(
                f"#JMP({format_num(rec.start + header)}, "
                f"{format_num(rec.end - 1 + header)})"
            )
        if mapping in ADDRESS_TYPES:
            for ptr in rec.pointers:
                addr = format_num(ptr.address + header)
                out.append(f"#{width.get(ptr.size, 'W16')}({addr})")
        # The end token a fixed string keeps out of its text is bytes Atlas
        # has to write.
        text = render(
            [replace(t, fallback=False) if is_hidden_end(t) else t for t in rec.tokens]
        )
        for line in strip_artificial(text, config):
            out.append(atlas_text(line, tables))
    return AtlasExport("\n".join(out) + "\n", files, notices)


# --- importing -----------------------------------------------------------------


@dataclass
class AtlasString:
    text: str
    insert_at: int | None
    pointers: tuple[int, ...]


@dataclass
class AtlasScript:
    strings: list[AtlasString] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    stopped_at: int | None = None


SUPPORTED = {
    "VAR",
    "ADDTBL",
    "ACTIVETBL",
    "JMP",
    "HDR",
    "W8",
    "W16",
    "W24",
    "W32",
    "WLB",
    "PTRTBL",
    "WRITE",
    "STRTYPE",
    "PASCALLEN",
    "PASCALTYPE",
    "FIXEDLENGTH",
    "STRINGALIGN",
    "SMA",
    "CREATEPTR",
    "ENDIANSWAP",
}
_CMD = re.compile(r"^\s*#([A-Z0-9]+)\((.*)\)\s*(//.*)?$")


def read_atlas(text: str) -> AtlasScript:
    """The strings of an Atlas script with where they go, for matching."""
    script = AtlasScript()
    insert: int | None = None
    pointers: list[int] = []
    body: list[str] = []
    body_at: int | None = None

    def flush() -> None:
        nonlocal body, body_at, pointers
        if body:
            script.strings.append(AtlasString("".join(body), body_at, tuple(pointers)))
            body = []
            pointers = []
        body_at = None

    for n, raw in enumerate(split_lines(text), start=1):
        if re.match(r"^[ \t]*//", raw) or not raw.strip():
            continue
        m = _CMD.match(raw)
        if not m:
            if body_at is None:
                body_at = insert
            body.append(_native_text(raw))
            continue
        flush()
        name, args = m.group(1), m.group(2)
        if name not in SUPPORTED:
            script.notices.append(f"line {n}: #{name} not supported; import stopped")
            script.stopped_at = n
            break
        if name == "ADDTBL":
            am = re.match(r'"([^"]*)"', args.strip())
            if am:
                script.tables.append(am.group(1))
        elif name == "JMP":
            insert = parse_num(args.split(",")[0])
        elif name in ("W8", "W16", "W24", "W32", "WLB"):
            parts = [p.strip() for p in args.split(",")]
            pointers.append(parse_num(parts[-1]))
    flush()
    return script


def _native_text(line: str) -> str:
    """Atlas text to native script form: ``<$XX>`` to ``[$XX]``, brackets kept."""
    out = re.sub(r"<\$([0-9A-Fa-f]{2})>", lambda m: f"[${m.group(1).upper()}]", line)
    out = re.sub(r"\(\$([0-9A-Fa-f]{2})\)", lambda m: f"[${m.group(1).upper()}]", out)
    return out
