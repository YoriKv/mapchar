"""Cartographer command files, read into block configurations.

Parsing follows ``docs/abcde/cartographer.md``: ``//`` comments cut anywhere,
``#COMMAND: value`` lines, one block per ``#END BLOCK``, ``#SUB TABLE`` at
file level, and the dependency rules between commands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    NextPointer,
    PointerTableSource,
    RangeSource,
)
from mapchar.core.errors import MapcharError
from mapchar.core.notices import Level, Notice


class CommandFileError(MapcharError):
    def __init__(self, message: str, line: int | None = None):
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


@dataclass
class CartographerBlock:
    name: str
    config: BlockConfig
    table_file: str
    """The ``#TABLE`` path as written."""
    table_id: str | None
    """``#TABLE ID`` when given, else ``None`` for the file's first table."""
    game_name: str | None = None
    comments: str = "No"
    atlas_ptrs: bool = False
    show_end_address: bool = True
    trim_trailing_newlines: bool = True
    sort_by_address: bool = False


@dataclass
class CommandFile:
    blocks: list[CartographerBlock]
    sub_tables: list[str] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)


COMMANDS: dict[str, str] = {
    "GAME NAME": "any",
    "BLOCK NAME": "any",
    "TYPE": r"NORMAL|FIXED_STRING\s*&&\s*FIXED_LINE|FIXED_STRING",
    "STRING LENGTH": "positive",
    "STRING END": r"Yes|No",
    "END CTRL": "any",
    "LINE LENGTH": "positive",
    "LINE END": r"Yes|No",
    "LINE CTRL": "any",
    "METHOD": r"POINTER_RELATIVE_PC|POINTER_RELATIVE|POINTER|RAW",
    "POINTER ENDIAN": r"BIG|LITTLE",
    "POINTER TABLE START": "non_negative",
    "POINTER TABLE STOP": "non_negative",
    "POINTER SIZE": "positive",
    "POINTER SPACE": "non_negative",
    "ATLAS PTRS": r"Yes|No",
    "BASE POINTER": "dec_or_hex",
    "RELATIVE PC": "any",
    "SCRIPT START": "non_negative",
    "SCRIPT STOP": "non_negative",
    "TABLE": "any",
    "TABLE ID": "any",
    "SUB TABLE": "any",
    "COMMENTS": r"Yes|No|Both",
    "END BLOCK": "none",
    "STRINGS PER POINTER": "non_negative",
    "STRING END REALIGN MULTIPLE": "positive",
    "STRING END REALIGN OFFSET": "non_negative",
    "AUTO JUMP START": "non_negative",
    "AUTO JUMP STOP": "non_negative",
    "SHOW END ADDRESS": r"Yes|No",
    "STRINGS END AT NEXT POINTER": r"Yes|No",
    "SORT OUTPUT BY STRING ADDRESS": r"Yes|No",
    "TRIM TRAILING NEWLINES": r"Yes|No",
}

_NUMBER = {
    "dec_or_hex": re.compile(r"^(-?\d+|-?\$[0-9A-Fa-f]+|\$-[0-9A-Fa-f]+)$"),
    "non_negative": re.compile(r"^(\d+|\$[0-9A-Fa-f]+)$"),
    "positive": re.compile(r"^(\d+|\$[0-9A-Fa-f]+)$"),
}

POINTER_SET = (
    "POINTER TABLE START",
    "POINTER TABLE STOP",
    "POINTER SIZE",
    "POINTER SPACE",
    "POINTER ENDIAN",
)
ALL_POINTER = POINTER_SET + ("ATLAS PTRS", "BASE POINTER", "STRINGS PER POINTER")


def parse_number(text: str) -> int:
    text = text.strip()
    if text.startswith("$-"):
        return -int(text[2:], 16)
    if text.startswith("-$"):
        return -int(text[2:], 16)
    if text.startswith("$"):
        return int(text[1:], 16)
    return int(text, 10)


def parse_command_file(text: str) -> CommandFile:
    if text.startswith("﻿"):
        text = text[1:]
    blocks: list[CartographerBlock] = []
    sub_tables: list[str] = []
    notices: list[Notice] = []
    current: dict[str, tuple[str, int]] = {}
    in_block = False
    previous_game: str | None = None

    for n, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        line = re.sub(r"\s*//.*", "", raw)
        if not line.strip():
            continue
        m = re.match(r"^#([A-Z][A-Z ]*?)(?::\s*(.*))?$", line.strip())
        if not m:
            raise CommandFileError(f"not a command: {raw.strip()!r}", n)
        name, value = m.group(1).strip(), m.group(2)
        if name not in COMMANDS:
            raise CommandFileError(f"unknown command #{name}", n)
        pattern = COMMANDS[name]
        if pattern == "none":
            if name == "END BLOCK":
                if not in_block:
                    raise CommandFileError("#END BLOCK without a block", n)
                block = _finish_block(current, previous_game, notices)
                previous_game = block.game_name
                blocks.append(block)
                current = {}
                in_block = False
            continue
        if value is None:
            raise CommandFileError(f"#{name} needs a value", n)
        value = value.strip()
        if pattern in _NUMBER:
            if not _NUMBER[pattern].match(value):
                raise CommandFileError(f"#{name}: bad number {value!r}", n)
            if pattern == "positive" and parse_number(value) == 0:
                raise CommandFileError(f"#{name}: must be positive", n)
        elif pattern != "any" and not re.fullmatch(pattern, value):
            raise CommandFileError(f"#{name}: bad value {value!r}", n)
        if name == "SUB TABLE":
            sub_tables.append(value)
            continue
        if name in current:
            raise CommandFileError(f"#{name} repeated in one block", n)
        current[name] = (value, n)
        in_block = True

    if in_block:
        raise CommandFileError("the last block has no #END BLOCK")
    if not blocks:
        raise CommandFileError("no blocks")
    return CommandFile(blocks, sub_tables, notices)


def _finish_block(
    cmds: dict[str, tuple[str, int]], previous_game: str | None, notices: list[Notice]
) -> CartographerBlock:
    def get(name: str) -> str | None:
        v = cmds.get(name)
        return v[0] if v else None

    def line_of(name: str) -> int | None:
        v = cmds.get(name)
        return v[1] if v else None

    for required in ("BLOCK NAME", "TYPE", "METHOD", "TABLE", "COMMENTS"):
        if required not in cmds:
            raise CommandFileError(
                f"block is missing #{required}", line_of("BLOCK NAME")
            )
    name = get("BLOCK NAME") or ""
    kind = re.sub(r"\s+", "", get("TYPE") or "")
    method = get("METHOD") or ""
    fixed = kind.startswith("FIXED_STRING")
    fixed_line = kind == "FIXED_STRING&&FIXED_LINE"

    def require(names, forbid=()):
        for r in names:
            if r not in cmds:
                raise CommandFileError(f"#{r} is required here", line_of("BLOCK NAME"))
        for f in forbid:
            if f in cmds:
                raise CommandFileError(f"#{f} is not allowed here", line_of(f))

    if kind == "NORMAL":
        require((), ("STRING LENGTH", "STRING END", "LINE LENGTH", "LINE END"))
    elif fixed_line:
        require(("STRING LENGTH", "STRING END", "LINE LENGTH", "LINE END"))
    else:
        require(("STRING LENGTH", "STRING END"), ("LINE LENGTH", "LINE END"))
    if method == "RAW":
        require(("SCRIPT START", "SCRIPT STOP"), ALL_POINTER)
    elif method == "POINTER":
        require(POINTER_SET + ("ATLAS PTRS",), ("BASE POINTER", "SCRIPT START"))
    else:
        require(POINTER_SET + ("ATLAS PTRS", "BASE POINTER"), ("SCRIPT START",))
    for a, b in (
        ("AUTO JUMP START", "AUTO JUMP STOP"),
        ("AUTO JUMP STOP", "AUTO JUMP START"),
    ):
        if a in cmds and b not in cmds:
            raise CommandFileError(f"#{a} needs #{b}", line_of(a))

    stop = parse_number(get("SCRIPT STOP")) if get("SCRIPT STOP") else None
    if method == "RAW":
        source = RangeSource(parse_number(get("SCRIPT START")), stop)
    else:
        mapping = "relative" if method == "POINTER_RELATIVE_PC" else "linear"
        size = parse_number(get("POINTER SIZE"))
        source = PointerTableSource(
            parse_number(get("POINTER TABLE START")),
            parse_number(get("POINTER TABLE STOP")),
            size,
            size + parse_number(get("POINTER SPACE")),
            "big" if get("POINTER ENDIAN") == "BIG" else "little",
            mapping,
            parse_number(get("BASE POINTER")) if get("BASE POINTER") else 0,
        )
    if fixed:
        string_type = FixedLength(parse_number(get("STRING LENGTH")), method != "RAW")
    elif get("STRINGS END AT NEXT POINTER") == "Yes" and method != "RAW":
        string_type = NextPointer()
    else:
        string_type = EndToken()
    realign = (
        parse_number(get("STRING END REALIGN MULTIPLE") or "0"),
        parse_number(get("STRING END REALIGN OFFSET") or "0"),
    )
    skips = ()
    if get("AUTO JUMP START"):
        skips = (
            (parse_number(get("AUTO JUMP START")), parse_number(get("AUTO JUMP STOP"))),
        )
    end_label = _label(get("END CTRL")) if get("END CTRL") else "end"
    line_label = _label(get("LINE CTRL")) if get("LINE CTRL") else "line"
    config = BlockConfig(
        source=source,
        string_type=string_type,
        table_id=get("TABLE ID") or "",
        strings_per_pointer=parse_number(get("STRINGS PER POINTER") or "1") or 1,
        realign=realign,
        skips=skips,
        line_length=parse_number(get("LINE LENGTH")) if fixed_line else 0,
        bound=stop,
        show_end=fixed and get("STRING END") == "Yes",
        end_label=end_label,
        line_label=line_label if fixed_line and get("LINE END") == "Yes" else "line",
    )
    if fixed_line and get("LINE END") != "Yes":
        notices.append(
            Notice(f"{name}: LINE END is No; lines are joined with [line]", Level.INFO)
        )
    if method != "RAW" and stop is None:
        notices.append(
            Notice(f"{name}: no SCRIPT STOP; strings run to end of file", Level.INFO)
        )
    return CartographerBlock(
        name=name,
        config=config,
        table_file=get("TABLE") or "",
        table_id=get("TABLE ID"),
        game_name=get("GAME NAME") or previous_game,
        comments=get("COMMENTS") or "No",
        atlas_ptrs=get("ATLAS PTRS") == "Yes",
        show_end_address=get("SHOW END ADDRESS") != "No",
        trim_trailing_newlines=get("TRIM TRAILING NEWLINES") != "No",
        sort_by_address=get("SORT OUTPUT BY STRING ADDRESS") == "Yes",
    )


def _label(marker: str) -> str:
    """A native code label from Cartographer's artificial marker text."""
    inner = marker.strip()
    if len(inner) >= 2 and inner[0] in "([<{" and inner[-1] in ")]>}":
        inner = inner[1:-1]
    inner = re.sub(r"\s+", "_", inner)
    return inner or "end"


# --- export ------------------------------------------------------------------


def write_command_file(
    name: str,
    config: BlockConfig,
    table_file: str,
    *,
    sub_tables: tuple[str, ...] = (),
    game_name: str | None = None,
    table_id: str | None = None,
) -> tuple[str, list[str]]:
    """A Cartographer command file for a block, and what it could not express."""
    from mapchar.core.block import FixedSource, Pascal, PointerListSource, WriteMode

    lines: list[str] = []
    notes: list[str] = []
    for sub in sub_tables:
        lines.append(f"#SUB TABLE: {sub}")
    if game_name:
        lines.append(f"#GAME NAME: {game_name}")
    lines.append(f"#BLOCK NAME: {name}")
    src = config.source
    st = config.string_type
    fixed_len = None
    if isinstance(st, FixedLength):
        fixed_len = st.length
    elif isinstance(src, FixedSource):
        fixed_len = src.length
        notes.append("fixed-string source written as a RAW range of one string")
    if isinstance(st, Pascal):
        notes.append("Pascal strings have no Cartographer form; written as NORMAL")
    if fixed_len is not None:
        kind = "FIXED_STRING && FIXED_LINE" if config.line_length else "FIXED_STRING"
        lines.append(f"#TYPE: {kind}")
        lines.append(f"#STRING LENGTH: {fixed_len}")
        lines.append(f"#STRING END: {'Yes' if config.show_end else 'No'}")
        if config.show_end:
            lines.append(f"#END CTRL: [{config.end_label}]")
        if config.line_length:
            lines.append(f"#LINE LENGTH: {config.line_length}")
            lines.append("#LINE END: Yes")
            lines.append(f"#LINE CTRL: [{config.line_label}]")
    else:
        lines.append("#TYPE: NORMAL")
    if isinstance(src, RangeSource | FixedSource):
        start = src.start
        stop = (
            src.stop
            if isinstance(src, RangeSource)
            else src.start + src.count * src.length
        )
        lines.append("#METHOD: RAW")
        lines.append(f"#SCRIPT START: ${start:X}")
        lines.append(f"#SCRIPT STOP: ${stop:X}")
    elif isinstance(src, PointerListSource):
        notes.append("pointer lists have no Cartographer form; block not exported")
        return "", notes
    else:
        if src.mapping_id == "relative":
            lines.append("#METHOD: POINTER_RELATIVE_PC")
            lines.append(f"#BASE POINTER: {_cart_num(src.offset)}")
        elif src.mapping_id == "linear" and src.offset:
            lines.append("#METHOD: POINTER_RELATIVE")
            lines.append(f"#BASE POINTER: {_cart_num(src.offset)}")
        elif src.mapping_id == "linear":
            lines.append("#METHOD: POINTER")
        else:
            notes.append(
                f"mapping {src.mapping_id!r} has no Cartographer form; "
                "written as POINTER"
            )
            lines.append("#METHOD: POINTER")
        lines.append(f"#POINTER ENDIAN: {'BIG' if src.endian == 'big' else 'LITTLE'}")
        lines.append(f"#POINTER TABLE START: ${src.start:X}")
        lines.append(f"#POINTER TABLE STOP: ${src.stop:X}")
        lines.append(f"#POINTER SIZE: {src.size}")
        lines.append(f"#POINTER SPACE: {max(src.stride - src.size, 0)}")
        lines.append("#ATLAS PTRS: Yes")
        if config.bound is not None:
            lines.append(f"#SCRIPT STOP: ${config.bound:X}")
        if config.strings_per_pointer != 1:
            lines.append(f"#STRINGS PER POINTER: {config.strings_per_pointer}")
        if isinstance(st, NextPointer):
            lines.append("#STRINGS END AT NEXT POINTER: Yes")
    if config.realign[0]:
        lines.append(f"#STRING END REALIGN MULTIPLE: {config.realign[0]}")
        lines.append(f"#STRING END REALIGN OFFSET: {config.realign[1]}")
    for i, (a, b) in enumerate(config.skips):
        if i:
            notes.append("only the first skip range has a Cartographer form")
            break
        lines.append(f"#AUTO JUMP START: ${a:X}")
        lines.append(f"#AUTO JUMP STOP: ${b:X}")
    lines.append(f"#TABLE: {table_file}")
    if table_id:
        lines.append(f"#TABLE ID: {table_id}")
    lines.append("#COMMENTS: No")
    lines.append("#END BLOCK")
    if config.write_mode is WriteMode.SLOTTED and not isinstance(
        src, RangeSource | FixedSource
    ):
        notes.append("slotted write mode is not expressible; Cartographer dumps only")
    return "\n".join(lines) + "\n", notes


def _cart_num(value: int) -> str:
    return f"${value:X}" if value >= 0 else f"$-{-value:X}"
