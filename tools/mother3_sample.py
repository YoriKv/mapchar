"""The Mother 3 sample: its tables and blocks, derived from the ROM itself.

Mother 3 (Japan) has no text a command file could name by hand: 13,000
strings sit in 582 offset tables and name lists inside the game's archives. So
nothing about it is checked in -- the character tables come out of the ROM's
fonts, and every block out of the archive headers -- and this module derives
both from ``Mother 3 (Japan).gba`` (CRC32 ``42AC9CB9``). Qt-free, so the tests
use it too; ``make_sample_projects.py`` writes the tables and project.

What the ROM holds, and where each fact comes from:

- **Archives.** Most data lives in archives: a u32 count and that many u32
  offsets from the archive's start, zero for an empty slot. The accessor at
  ``$0800289C`` returns ``archive + offset[i]``.
- **Characters** are u16 indices into the dialogue font at ``$CE39F8`` (22
  bytes a glyph) and the small font at ``$D0B010`` (10 bytes): each glyph starts
  with its Shift-JIS code, in Shift-JIS order, 7,332 of them (the loader at
  ``$0800A0E0`` sets both). Codes that are not valid Shift-JIS are blank
  glyphs; ``$00AC`` is the one the script uses as its space.
- **Control codes** are ``$FFxx``. The table at ``$D2DE58`` gives each code's
  operand count in words, read by ``$08022ED0`` for every text box; the
  renderer at ``$08009B98`` draws ``FF01`` as a line break and ``FF05`` as a
  palette change, and ``$08021BDC`` indents a line to centre it for ``FF09``.
  ``FFFF`` ends a string.
- **``FF0B``** draws the rest of the string from the 90-glyph Mr. Saturn font
  at ``$D1CE78`` (16x16, the left and right bytes of each row stored apart),
  whose codes run ``あ``-``ん`` in gojūon order with separate dakuten marks.
- **Battle messages** go through their own substitution loop (``$080734A0``):
  there ``FF10``-``FF37`` insert names and pause, and none takes an operand, so
  ``FF20`` and ``FF21`` mean something else than in the script.
- **Where the text is** was found by diffing the ROM against the English fan
  translation's patch: every archive entry the patch rewrites that holds text
  is below.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace

ROM_NAME = "Mother 3 (Japan).gba"

FONT_CODES = 0xD0B010
"""The small font: a Shift-JIS code and an 8x8 glyph per character."""
FONT_STRIDE = 10
FONT_SIZE = 7332
SPACE = 0xAC
"""The blank glyph the script separates words with."""

LENGTHS = 0xD2DE58
"""``(u16 operand words, u16 code)`` records to ``$FFFF``, read by ``$08022ED0``."""

MAIN_SCRIPT = 0x136A6F4
"""Archive of every map's text, named by the literal at ``$08027F54``: entries
``2g`` and ``2g+1`` are map ``g``'s offset table and its strings."""
NAMES = 0xD1EE78
"""Archive of item, enemy and PSI names and descriptions (16 entries)."""
MENUS = 0x1B8FFC0
"""Archive of the menus' graphics and text (94 entries)."""
DEBUG = 0x1AF3790
"""Archive of the debug menu (66 entries)."""

# The Mr. Saturn font's codes, from its glyphs. $3D is a mark no string uses.
SATURN = (
    "　あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよ"
    "らりるれろわをん「」ぁぃぅぇぉゃゅょっー゙゚"
)
SATURN_EXTRA = {0x3E: "◆", 0x3F: "。", 0x40: "？", 0x41: "！", 0x42: " "}

# What each script code was seen doing, where its label does not say.
SCRIPT_NOTES = {
    0xFF00: "Follows every [FF03]: the next page of a text box.",
    0xFF02: "Ends a row of choices.",
    0xFF03: "Precedes every [FF00]: the end of a page.",
    0xFF04: "Between dots in drawn-out speech: a pause.",
    0xFF06: "Before a row of choices; the operand is how many (2 for はい/いいえ).",
    0xFF07: "Ends some strings; not in the length table.",
    0xFF08: "Before animal noises (チュン).",
    0xFF0A: "Indents a line like [center] does, in another mode.",
    0xFF0C: "Ends shouted lines.",
    0xFF21: "Inserts a name; the operand is often $FFF0/$FFF1.",
    0xFF22: "Inserts a name.",
    0xFF23: "Inserts a name.",
    0xFF24: "Inserts a name.",
    0xFF25: "Inserts a name, after ＰＫ.",
    0xFF26: "Takes two operands.",
    0xFF42: "Inserts a name.",
    0xFF45: "Inserts an item or food name.",
    0xFF46: "Inserts a PSI name suffix, after ＰＫ.",
    0xFF47: "Inserts a name, before さん.",
    0xFF80: "Inserts a number; the operand is often $FFF0-$FFF5.",
    0xFF81: "Inserts an item name.",
    0xFF82: "Inserts an amount of DP.",
    0xFFE0: "Inserts who can use a PSI.",
}
SCRIPT_LABELS = {0xFF01: "line", 0xFF05: "color", 0xFF09: "center", 0xFF0B: "saturn"}
BATTLE_CODES = (0xFF01, 0xFF02, *range(0xFF10, 0xFF22), *range(0xFF30, 0xFF38))


@dataclass(frozen=True)
class Block:
    name: str
    spec: str
    """The block's configuration line, as a native script's ``@block`` spells it."""
    folder: str | None = None
    """The Files panel folder the project puts the block in; ``None`` for none."""


FOLDERS = {
    "Names": (
        "Item names",
        "Character names",
        "Battler names",
        "Party battle names",
        "Enemy names",
        "PSI names",
        "Status names",
        "Area names",
    ),
    "Descriptions": (
        "Item descriptions",
        "Enemy profiles",
        "PSI descriptions",
        "Battle command descriptions",
    ),
    "Battle": ("Battle commands", "Battle text"),
    "Menus": (
        "Menu text",
        "Menu labels",
        "Save messages",
        "Sound player titles",
        "Debug menu",
    ),
}
"""Which folder each block goes in, by name; a block named in none stands
directly under the ROM."""


def _u16(rom: bytes, at: int) -> int:
    return struct.unpack_from("<H", rom, at)[0]


def _u32(rom: bytes, at: int) -> int:
    return struct.unpack_from("<I", rom, at)[0]


def _key(value: int) -> str:
    """A u16's table key: its two bytes as the ROM stores them."""
    return f"{value & 0xFF:02X}{value >> 8:02X}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def archive(rom: bytes, base: int) -> list[int | None]:
    """Where each entry of an archive starts, ``None`` for an empty slot."""
    count = _u32(rom, base)
    offsets = struct.unpack_from(f"<{count}I", rom, base + 4)
    return [base + o if o else None for o in offsets]


def characters(rom: bytes) -> dict[int, str]:
    """Every glyph index whose Shift-JIS code decodes, with its character."""
    chars = {}
    for i in range(FONT_SIZE):
        at = FONT_CODES + i * FONT_STRIDE
        try:
            chars[i] = rom[at : at + 2].decode("cp932")
        except UnicodeDecodeError:
            pass
    chars[SPACE] = " "
    return chars


def operand_counts(rom: bytes) -> dict[int, int]:
    """Each script control code's operand count in words, from ``$D2DE58``."""
    counts = {}
    at = LENGTHS
    while True:
        words, code = struct.unpack_from("<2H", rom, at)
        counts[code] = words
        at += 4
        if code == 0xFFFF:
            return counts


def _header(table_id: str, comment: str, includes: tuple[str, ...] = ()) -> list[str]:
    lines = ["@mapchar table 1"]
    lines += [f"# {line}" if line else "#" for line in comment.split("\n")]
    return [*lines, f"@table {table_id}", *(f"@include {i}" for i in includes), ""]


EFFECTS = {0xFF00: "page", 0xFF01: "newline", 0xFF04: "pause"}
"""What a script code does to the text box: ``FF00`` opens the next page,
``FF01`` breaks the line, ``FF04`` waits."""


def _code_line(code: int, label: str, words: int) -> str:
    effect = EFFECTS.get(code)
    key = _key(code) + (f"{{{effect}}}" if effect else "")
    if words:
        return f"${key}=[{label}]" + ",u16" * words
    return f"{key}=[{label}]"


def _script_codes(rom: bytes) -> dict[int, tuple[str, int]]:
    """Every script control code but ``FFFF`` and ``FF0B``: its label and its
    operand count in words."""
    counts = operand_counts(rom)
    counts[0xFF07] = 0  # used, and not in the table: the lookup's default
    return {
        code: (SCRIPT_LABELS.get(code, f"FF{code & 0xFF:02X}"), counts[code])
        for code in sorted(counts)
        if code not in (0xFFFF, 0xFF0B)
    }


def table_files(rom: bytes) -> dict[str, str]:
    """The sample's four native tables, by file name.

    ``m3codes`` holds the script's control codes, which both fonts share: the
    dialogue table ``m3`` and Mr. Saturn's ``saturn`` each include it and add
    their own characters. ``saturn`` includes the codes rather than letting its
    switch frame fall through to ``m3``: falling through would also reach the
    dialogue font's 7,000 characters, and a string typed inside ``[saturn]``
    would then encode them into bytes the game draws from the 90-glyph Saturn
    font. ``m3battle`` is ``m3`` with the codes the battle message loop reads
    differently.
    """
    chars = [
        f"{_key(i)}={_escape(text)}" for i, text in sorted(characters(rom).items())
    ]
    script = _script_codes(rom)
    codes = ["/FFFF=[end]"]
    for code, (label, words) in script.items():
        note = SCRIPT_NOTES.get(code)
        codes += [*([f"# {note}"] if note else []), _code_line(code, label, words)]
    battle = []
    for code in BATTLE_CODES:
        line = _code_line(code, "line" if code == 0xFF01 else f"FF{code & 0xFF:02X}", 0)
        label, words = script.get(code, ("", -1))
        if line != _code_line(code, label, words):
            battle.append(line)
    saturn_chars = dict(enumerate(SATURN)) | SATURN_EXTRA
    files = {
        "m3codes.tbl": [
            *_header(
                "m3codes",
                "Mother 3 (Japan): the script's control codes, with the operand\n"
                "counts the table at $D2DE58 gives them, shared by the dialogue\n"
                "and Mr. Saturn fonts. Generated from the ROM by\n"
                "tools/mother3_sample.py.",
            ),
            *codes,
        ],
        "m3.tbl": [
            *_header(
                "m3",
                "Mother 3 (Japan): u16 glyph indices into the Shift-JIS-ordered fonts\n"
                "at $CE39F8 and $D0B010, over the script's control codes. Generated\n"
                "from the ROM by tools/mother3_sample.py.",
                ("m3codes",),
            ),
            f"!{_key(0xFF0B)}=[saturn] @saturn:*",
            "",
            *chars,
        ],
        "m3battle.tbl": [
            *_header(
                "m3battle",
                "Mother 3 (Japan) battle messages: the script's table, with the\n"
                "codes the battle message loop at $080734A0 substitutes, none of\n"
                "which takes an operand. Generated from the ROM by\n"
                "tools/mother3_sample.py.",
                ("m3",),
            ),
            *battle,
        ],
        "saturn.tbl": [
            *_header(
                "saturn",
                "The Mr. Saturn font at $D1CE78, which [saturn] switches to for the\n"
                "rest of a string, over the script's control codes. Generated by\n"
                "tools/mother3_sample.py.",
                ("m3codes",),
            ),
            *(f"{_key(i)}={t}" for i, t in sorted(saturn_chars.items())),
        ],
    }
    return {name: "\n".join(lines) + "\n" for name, lines in files.items()}


def _string_end(rom: bytes, at: int) -> int:
    while _u16(rom, at) != 0xFFFF:
        at += 2
    return at + 2


def _offsets(
    rom: bytes, name: str, table: int, count: int, base: int, table_id: str
) -> Block:
    """A block over ``count`` u16 offsets at ``table`` into strings at ``base``.

    Offset zero names the ``FFFF`` that opens a string list, ahead of its
    count: a slot nothing uses. Where a table has such slots the block lists
    its other pointers instead, so the header never reads as a string that a
    packed write would move.
    """
    offsets = struct.unpack_from(f"<{count}H", rom, table)
    common = f"size=2 endian=little mapping=linear offset={base} bank=0"
    if 0 in offsets:
        addresses = ",".join(f"${table + 2 * i:X}" for i, o in enumerate(offsets) if o)
        source = f"source=list addresses={addresses} {common}"
    else:
        stop = table + 2 * count
        source = f"source=pointers start=${table:X} stop=${stop:X} stride=2 {common}"
    spec = f"{source} type=end table={table_id}"
    # A string that is the tail of another cannot be packed: its bytes would be
    # written twice. Kept in place, every string can still be edited.
    starts = sorted({base + o for o in offsets if o})
    if any(_string_end(rom, a) > b for a, b in zip(starts, starts[1:], strict=False)):
        spec += " mode=slotted"
    return Block(name, spec)


def _paired(
    rom: bytes, name: str, base: int, index: int, table_id: str = "m3"
) -> Block:
    """Entries ``index`` (offsets) and ``index + 1``: ``FFFF``, the count, and
    the strings the offsets are from."""
    table, text = archive(rom, base)[index : index + 2]
    assert table is not None and text is not None, (hex(base), index)
    return _offsets(rom, name, table, _u16(rom, text + 2), text, table_id)


def _names(rom: bytes, name: str, index: int) -> Block:
    """A fixed-width name list: u16 width and count, then the names, each
    padded with ``FFFF``."""
    start = archive(rom, NAMES)[index]
    assert start is not None, index
    width, count = struct.unpack_from("<2H", rom, start)
    stop = start + 4 + 2 * width * count
    return Block(
        name,
        f"source=range start=${start + 4:X} stop=${stop:X} "
        f"type=fixed:{2 * width}:stop table=m3",
    )


def _bxt(rom: bytes, name: str, at: int, table_id: str = "m3") -> Block:
    """A ``bxt`` chunk: the tag, u32 1, a u32 count, u16 offsets from the tag."""
    assert rom[at : at + 4] == b"bxt ", hex(at)
    return _offsets(rom, name, at + 12, _u32(rom, at + 8), at, table_id)


def blocks(rom: bytes) -> list[Block]:
    """Every text block of the sample, in project order."""
    out = [
        _names(rom, "Item names", 2),
        _paired(rom, "Item descriptions", NAMES, 3),
        _names(rom, "Character names", 6),
        _names(rom, "Battler names", 5),
        _names(rom, "Party battle names", 12),
        _names(rom, "Enemy names", 7),
        _paired(rom, "Enemy profiles", MENUS, 92),
        _names(rom, "PSI names", 8),
        _paired(rom, "PSI descriptions", NAMES, 9),
        _names(rom, "Status names", 11),
        _names(rom, "Battle commands", 13),
        _paired(rom, "Battle command descriptions", NAMES, 14),
        _bxt(rom, "Battle text", 0x1CFFD98, "m3battle"),
        _paired(rom, "Area names", NAMES, 0),
        _paired(rom, "Menu text", MENUS, 88),
        _paired(rom, "Menu labels", MENUS, 90),
        _bxt(rom, "Save messages", 0x1D0BC24),
        _bxt(rom, "Sound player titles", 0x1C8F390),
        _paired(rom, "Debug menu", DEBUG, 36),
    ]
    script = archive(rom, MAIN_SCRIPT)
    for group in range(len(script) // 2):
        if script[2 * group] is not None:
            out.append(_paired(rom, f"Script {group:04d}", MAIN_SCRIPT, 2 * group))
    folder_of = {name: folder for folder, names in FOLDERS.items() for name in names}
    return [replace(b, folder=folder_of.get(b.name)) for b in out]
