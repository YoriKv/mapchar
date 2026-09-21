"""The block-configuration line: how a reading is written down.

``source=range start=$0 stop=$100 type=end table=main`` — the field line that
says where a block's strings are and how they are cut. One spelling covers the
three places a reading is kept: the project file stores it per block
(:mod:`mapchar.project.projectfile`), a script carries it on its ``@block``
directive (:mod:`mapchar.project.formats.script`), and a file's session
reading holds one of its own. A field left out is the default
:class:`~mapchar.core.block.BlockConfig` has, so a line names only what is not.
"""

from __future__ import annotations

from mapchar.core.block import (
    MAX_RECORD_HEADER,
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NestedPointerSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    Source,
    StringType,
    WriteMode,
)
from mapchar.core.fill import DEFAULT_FILL, format_fill, parse_fill
from mapchar.core.numbers import format_num, parse_num


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
    elif isinstance(s, NestedPointerSource):
        parts += [
            "source=nested",
            f"start={format_num(s.start)}",
            f"stop={format_num(s.stop)}",
            f"size={s.size}",
            f"stride={s.stride}",
            f"endian={s.endian}",
            f"mapping={s.mapping_id}",
            f"offset={s.offset}",
            f"bank={s.bank}",
        ]
    null = getattr(s, "null", None)
    if null is not None:
        parts.append(f"null={format_num(null)}")
    if isinstance(s, NestedPointerSource):
        parts += [f"inner_size={s.inner_size}", f"inner_endian={s.inner_endian}"]
        if s.inner_null is not None:
            parts.append(f"inner_null={format_num(s.inner_null)}")
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
    if config.run_to_next:
        last = config.strings_per_pointer
        parts.append("spp=next" + (f":{last}" if last != 1 else ""))
    elif config.strings_per_pointer != 1:
        parts.append(f"spp={config.strings_per_pointer}")
    if config.realign[0]:
        parts.append(f"realign={config.realign[0]}:{config.realign[1]}")
    if config.skips:
        parts.append(
            "skips="
            + ",".join(f"{format_num(a)}>{format_num(b)}" for a, b in config.skips)
        )
    if config.record_header:
        parts.append(f"header={config.record_header}")
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
    if config.fill != DEFAULT_FILL:
        parts.append(f"fill={format_fill(config.fill)}")
    if config.end_is_fill:
        parts.append("end_is_fill=1")
    return " ".join(parts)


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
            _null(fields, "null"),
        )
    elif kind == "list":
        source = PointerListSource(
            tuple(parse_num(a) for a in fields["addresses"].split(",") if a),
            int(fields["size"]),
            fields.get("endian", "little"),
            fields.get("mapping", "linear"),
            int(fields.get("offset", "0")),
            int(fields.get("bank", "0")),
            _null(fields, "null"),
        )
    elif kind == "nested":
        size = int(fields["size"])
        source = NestedPointerSource(
            parse_num(fields["start"]),
            parse_num(fields["stop"]),
            size,
            int(fields.get("stride", str(2 * size))),
            fields.get("endian", "little"),
            fields.get("mapping", "linear"),
            int(fields.get("offset", "0")),
            int(fields.get("bank", "0")),
            int(fields.get("inner_size", "2")),
            fields.get("inner_endian", "little"),
            _null(fields, "null"),
            _null(fields, "inner_null"),
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
    header = int(fields.get("header", "0"))
    if not 0 <= header <= MAX_RECORD_HEADER:
        raise ValueError(f"header={header} is not 0 to {MAX_RECORD_HEADER}")
    skips = ()
    if fields.get("skips"):
        skips = tuple(
            (parse_num(a), parse_num(b))
            for a, b in (pair.split(">") for pair in fields["skips"].split(","))
        )
    # ``spp=N`` strings a pointer, or ``spp=next[:N]``: each run to the next
    # pointer's target, the last of them N strings.
    spp = fields.get("spp", "1").split(":")
    run_to_next = spp[0] == "next"
    if run_to_next:
        spp = spp[1:] or ["1"]
    return BlockConfig(
        source=source,
        string_type=string_type,
        table_id=fields.get("table", ""),
        strings_per_pointer=int(spp[0]),
        run_to_next=run_to_next,
        realign=realign,
        skips=skips,
        header=header,
        line_length=int(fields.get("lines", "0")),
        bound=parse_num(fields["bound"]) if "bound" in fields else None,
        write_mode=WriteMode(fields["mode"]) if "mode" in fields else None,
        fill=parse_fill(fields["fill"]) if "fill" in fields else DEFAULT_FILL,
        end_is_fill=fields.get("end_is_fill", "0") == "1",
        show_end="show_end" in fields,
        end_label=fields.get("show_end", "end"),
        line_label=fields.get("line_label", "line"),
    )


def _null(fields: dict[str, str], key: str) -> int | None:
    """A null pointer value the fields name, or ``None``."""
    return parse_num(fields[key]) if fields.get(key) else None
