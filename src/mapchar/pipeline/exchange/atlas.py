"""Atlas scripts: export for abcde's Atlas module, and a subset importer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mapchar.core.block import (
    BlockConfig,
    FixedLength,
    FixedSource,
    Pascal,
    StringRecord,
    WriteMode,
)
from mapchar.core.table import EntryKind, Table, TableSet
from mapchar.core.tokens import (
    CodeRef,
    TextRun,
    escape_text,
    operand_values,
    parse_text,
)

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


# --- tables in the abcde dialect ------------------------------------------


def _abcde_key(bits: str) -> str:
    if len(bits) % 4 == 0:
        return "".join(
            format(int(bits[i : i + 4], 2), "X") for i in range(0, len(bits), 4)
        )
    return "%" + bits


def write_abcde_tables(tables: list[Table]) -> str:
    lines: list[str] = []
    for table in tables:
        lines.append(f"@{table.id}")
        for e in table.sorted_entries():
            key = _abcde_key(e.bits)
            weight = f"<{e.weight}>" if e.weight != 1 else ""
            if e.kind is EntryKind.TEXT:
                lines.append(f"{key}{weight}={_abcde_text(e.text)}")
            elif e.kind is EntryKind.END:
                lines.append(f"/{key}{weight}={_abcde_text(e.text)}")
            elif e.kind is EntryKind.RETURN:
                lines.append(f"!{key}{weight}=,-1")
            elif e.kind is EntryKind.CODE:
                n = sum(o.bits for o in e.operands) // 8
                lines.append(f"!{key}{weight}=<[{e.text}]>,{n}")
            else:
                params = []
                for p in e.params:
                    if p.table_id == "return":
                        params.append("-1")
                        continue
                    stop = p.stop
                    if stop.count is not None:
                        m = str(stop.count)
                    elif stop.fallback is not None:
                        fb = stop.fallback
                        m = "$" + _abcde_key(fb) if len(fb) % 8 == 0 else "%" + fb
                    else:
                        m = "0"
                    if p.table_id == "raw":
                        params.append(m + ("+" if p.shared else ""))
                    elif p.table_id == "bits":
                        params.append(f"<binary>:{m}" + ("+" if p.shared else ""))
                    else:
                        params.append(
                            f"<@{p.table_id}>:{m}" + ("+" if p.shared else "")
                        )
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
            entry = None
            for t in tables.tables.values():
                entry = t.labels.get(ref.label)
                if entry is not None:
                    break
            if entry is None or entry.kind is not EntryKind.CODE:
                out.append(f"[{ref.label} {' '.join(ref.words)}]")
                continue
            values = operand_values(entry, ref.words)
            bits = "".join(
                spec.bits_of(v) for spec, v in zip(entry.operands, values, strict=True)
            )
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
    table_files: dict[str, list[Table]],
) -> AtlasExport:
    """An Atlas script plus abcde-dialect tables that insert the block."""
    notices: list[str] = []
    out = [f"// mapchar: {block_name}"]
    # abcde loads one table per file unless told otherwise, so a file that
    # holds several tables is split into one file per table.
    split: dict[str, list[Table]] = {}
    for name, ts in table_files.items():
        if len(ts) <= 1:
            split[name] = ts
        else:
            stem = name.rsplit(".", 1)[0]
            for t in ts:
                split[f"{stem}_{t.id}.tbl"] = [t]
    files = {name: write_abcde_tables(ts) for name, ts in split.items()}
    var_by_id: dict[str, str] = {}
    for n, (name, ts) in enumerate(split.items()):
        var = f"Table_{n}"
        out.append(f"#VAR({var}, TABLE)")
        out.append(f'#ADDTBL("{name}", {var})')
        if ts:
            var_by_id[ts[0].id] = var
    start_var = var_by_id.get(tables.start.id)
    if start_var:
        out.append(f"#ACTIVETBL({start_var})")
    else:
        out.append(f"#ACTIVETBL(@{tables.start.id})")
    src = config.source
    mapping = getattr(src, "mapping_id", "linear")
    if config.has_pointers:
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
            out.append(f"#HDR({_num(offset)})")
    st = config.string_type
    if isinstance(st, Pascal):
        out.append('#STRTYPE("PASCAL")')
        out.append(f"#PASCALLEN({st.width})")
        if st.counts_tokens:
            out.append('#PASCALTYPE("TOKENS")')
    fixed_len = None
    if isinstance(st, FixedLength):
        fixed_len = st.length
    elif isinstance(src, FixedSource):
        fixed_len = src.length
    if fixed_len is not None:
        out.append(f"#FIXEDLENGTH({fixed_len}, {_num(config.fill)})")
    if config.realign[0]:
        out.append(f"#STRINGALIGN({config.realign[0]})")
        if config.realign[1]:
            notices.append("realign offset has no Atlas form")
    mode = config.effective_write_mode
    packed = mode is WriteMode.PACKED
    if packed and strings:
        bound = config.bound if config.bound is not None else getattr(src, "stop", None)
        if bound is not None:
            out.append(f"#JMP({_num(strings[0].start)}, {_num(bound - 1)})")
        else:
            out.append(f"#JMP({_num(strings[0].start)})")
    width = {1: "W8", 2: "W16", 3: "W24", 4: "W32"}
    for rec in strings:
        out.append("")
        out.append(f"// #{rec.index}")
        if not packed:
            out.append(f"#JMP({_num(rec.start)}, {_num(rec.end - 1)})")
        if mapping in ADDRESS_TYPES:
            for ptr in rec.pointers:
                out.append(f"#{width.get(ptr.size, 'W16')}({_num(ptr.address)})")
        text = rec.translation if rec.translation is not None else rec.original_text()
        for line in _strip_artificial_codes(text, config).split("\n"):
            out.append(atlas_text(line, tables))
    return AtlasExport("\n".join(out) + "\n", files, notices)


def _strip_artificial_codes(text: str, config: BlockConfig) -> str:
    if config.show_end and text.rstrip("\n").endswith(f"[{config.end_label}]"):
        text = text.rstrip("\n")[: -len(f"[{config.end_label}]")]
    if config.line_length:
        text = text.replace(f"[{config.line_label}]", "")
    return text


def _num(value: int) -> str:
    return f"${value:X}" if value >= 0 else f"$-{-value:X}"


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


def _atlas_num(word: str) -> int:
    word = word.strip()
    if word.startswith("$-"):
        return -int(word[2:], 16)
    if word.startswith("-$"):
        return -int(word[2:], 16)
    if word.startswith("$"):
        return int(word[1:], 16)
    return int(word)


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

    for n, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
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
            insert = _atlas_num(args.split(",")[0])
        elif name in ("W8", "W16", "W24", "W32", "WLB"):
            parts = [p.strip() for p in args.split(",")]
            pointers.append(_atlas_num(parts[-1]))
    flush()
    return script


def _native_text(line: str) -> str:
    """Atlas text to native script form: ``<$XX>`` to ``[$XX]``, brackets kept."""
    out = re.sub(r"<\$([0-9A-Fa-f]{2})>", lambda m: f"[${m.group(1).upper()}]", line)
    out = re.sub(r"\(\$([0-9A-Fa-f]{2})\)", lambda m: f"[${m.group(1).upper()}]", out)
    return out
