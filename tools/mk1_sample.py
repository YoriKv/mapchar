"""The Mortal Kombat sample: its tables and blocks, derived from the ROM itself.

Mortal Kombat (USA) for the Game Boy (``gb_mk1_us.gb``, CRC32 ``64F5460C``,
256 KiB, MBC1, Probe Software 1993) keeps its text as ASCII in banks 0 and 1.
Both sit at their own addresses -- bank 0 is fixed and bank 1 is the default
page -- so a file offset *is* the CPU address for every string here. The bank
register is ``$3FFF`` (``ea ff 3f``), its shadow ``$DFFF``.

Almost every string is **inline**: a ``call`` to a printer is followed by the
string, and the printer pops its return address, reads the text, and jumps
past the terminator. No pointer names these strings; each is found from the
``call`` before it, and a block over a screen's strings is a range from the
first to the last, with a skip range over the code between two strings.
Inline text is sized by the code around it, so it is read and written slotted.
Every printer returns to the byte after the terminator (``jp (hl)``, or
``push de / ret``), so a string written shorter has its padding *executed*:
the blocks pad with ``$00``, which is ``nop``, and the end token being that
fill, a shortened string reads back as itself and grows back later. The
default ``$FF`` is ``rst $38``, and ``$0038`` holds another ``rst $38``: the
stack runs down through RAM and the game hangs on a white screen.

What the ROM holds, and where each fact comes from:

- **``1:$4D94``**, the screen printer (menus, legal, high scores, the
  biographies, link and select screens, the ending): ``00`` ends, ``01 r c``
  moves to row ``r`` column ``c`` of the ``$9800`` map, ``02 r c h w`` draws a
  box, and anything else is a character. ``$2101`` turns a character into a
  tile of the main font (``1:$79F9``, RNC method 2, 54 glyphs at VRAM
  ``$8000``): space 0, ``A``-``Z`` 1-26 (``c - $40``, which every code from
  ``$41`` up takes), digits 27-36, ``.`` ``,`` 37-38, ``@`` ``$9C`` ``#``
  ``_`` to the ``(C)``, ``(R)``, ``TM`` and dotted-rule glyphs. Every other
  code below ``$30`` is the tile of that number. ``$73`` and ``$74`` are the
  only codes that reach the two rule glyphs, ``$33`` and ``$34``. The map is
  written at ``$9800 + 32r + c`` with nothing clipped: the screen is rows
  0-17 and columns 0-19.
- **Picture screens**: ``1:$4EF3`` unpacks the portraits to VRAM ``$8320``
  (the credits screen and the PRESS START that blinks on it, the biographies,
  link and select screens) and ``1:$4E81`` Goro's picture there, both over
  tiles ``$32``-``$35``: ``TM``, the two rules and ``_`` draw picture tiles on
  those screens, so their blocks read through a table without them.
- **``$20BC``**, the fight printer (ROUND, FIGHT, FINISH HIM, the bonuses):
  ``00`` ends and ``01 r c`` moves, with no box code; ``$214C`` draws each
  character through the same ``$2101`` into a RAM copy of the main font at
  ``$D200``, outlined over the arena. Each non-space character takes a tile
  from ``$152D``'s pool, 80 a frame shared with the fighters (``$1651``); an
  empty pool draws into tile 0. The longest banner draws 21.
- **``$1C20``**, the HUD-line printer: copies a ``00``-terminated string into
  the 20-character HUD line ``$D65E``, drawn by ``$1C5B`` through the HUD
  font (``1:$7C13``, 1bpp, glyph = code ``- $20``, ASCII ``$20``-``$5A``).
  Its two strings are 20 spaces that clear the line before the names go in.
- **The fighter names**, 14 Pascal strings in 9-byte slots at ``$1A31``, and
  a second list at ``$1AB2`` that ``$1B34`` reads instead while ``$D632`` is
  set (nothing in the code sets it: the staff names). ``$1B4E``/``$1B61``
  copy one into the HUD line, ``1:$5243`` centres one under a portrait,
  ``$1F94`` prints the winner. Only the length's worth of a slot is read, so
  a block realigns to the next slot after each name, and a name may take up
  its whole slot: 8 letters. All three loop ``dec / jp nz`` on the length, so
  a name of none runs 256 times. A name goes through both fonts, so its table
  holds only what both draw alike. Fighter ids run 0-8; nothing reads
  entries 9-13.
- **The high-score table**, seven 8-byte records at ``1:$449A`` that
  ``1:$448A`` copies to RAM: three initials and five bytes of streak and BCD
  score, realigned over. ``1:$4443`` prints the initials through ``$2101``,
  all three whatever they hold, so shorter initials pad with spaces.
- **``$02F2``**, the boot self-test's printer: ``BOOT OK.`` and ``RAMTEST
  FAILED !`` as ASCII - ``$20`` tiles. Nothing calls the routine that prints
  them, and no font is loaded for them.
"""

from __future__ import annotations

from dataclasses import dataclass

ROM_NAME = "gb_mk1_us.gb"

SCREEN_PRINTER = 0x4D94
"""``1:$4D94``: ``00`` end, ``01 r c`` position, ``02 r c h w`` box."""
FIGHT_PRINTER = 0x20BC
"""``$20BC``: ``00`` end, ``01 r c`` position."""
HUD_PRINTER = 0x1C20
"""``$1C20``: ``00`` end; A is the HUD column."""
DEBUG_PRINTER = 0x02F2
"""``$02F2``: ``00`` end, ``01 r c`` position; ASCII - ``$20``."""
OPERANDS = {
    SCREEN_PRINTER: {0x01: 2, 0x02: 4},
    FIGHT_PRINTER: {0x01: 2},
    HUD_PRINTER: {},
    DEBUG_PRINTER: {0x01: 2},
}
"""Each printer's codes and how many bytes follow each."""

TRANSLATE = (0x2101, 0x214C)
"""``$2101``: character -> main-font tile, a run of ``cp n / jp z`` to
``ld a,t / ret``, then letters and digits by arithmetic."""
LETTER_BASE = 0x40
"""``$212F``: ``sub $41 / inc a`` -- a letter's tile is its code - ``$40``."""
DIGIT_BASE = 0x1B
"""``$2133``: ``sub $30 / add a,$1B``."""
UNIQUE_BY_ARITHMETIC = {0x73: "—", 0x74: "‾"}
"""Codes past ``Z`` whose ``c - $40`` is a glyph no ``cp`` names: the two
rules, tiles ``$33`` and ``$34``."""
GLYPH = {0x30: "©", 0x31: "®", 0x32: "™", 0x35: "_", 0x25: ".", 0x26: ",", 0x00: " "}
"""What the main font's non-alphanumeric glyphs are, read off the sheet."""

NAMES = (0x1A31, 0x1AB2, 0x1B34)
"""The two name lists and the end of the second: ``$1B34`` reads ``$1A31 +
9n``, or ``$1AB2 + 9n`` while ``$D632`` is set."""
NAME_LOOKUP = 0x1B3E
"""``ld bc,$1A31`` in ``$1B34``."""
NAME_SLOT = 9

SCORES = (0x449A, 7, 8, 3)
"""The high-score defaults: address, records, record size, initials."""
SCORE_COPY = 0x448A
"""``1:$448A``: ``ld de,$D83C``, the copy of the defaults to RAM."""

GROUPS = (
    # name, folder, printer, the call sites, in address order
    ("Legal", "Screens", SCREEN_PRINTER, (0x4097,)),
    ("Midway presents", "Screens", SCREEN_PRINTER, (0x4056,)),
    ("Press start", "Screens", SCREEN_PRINTER, (0x41B9, 0x41CE)),
    ("Title menu", "Screens", SCREEN_PRINTER, (0x41FD, 0x4227)),
    ("Initials", "Screens", SCREEN_PRINTER, (0x42F0,)),
    ("High scores", "Screens", SCREEN_PRINTER, (0x43F4,)),
    ("Credits", "Screens", SCREEN_PRINTER, (0x4558,)),
    ("Goro lives", "Screens", SCREEN_PRINTER, (0x4633, 0x464E)),
    (
        "Biographies",
        "Screens",
        SCREEN_PRINTER,
        (0x4678, 0x469D, 0x46E0, 0x476B, 0x4811, 0x48A9, 0x4943, 0x49FF, 0x4A70),
    ),
    ("Link", "Screens", SCREEN_PRINTER, (0x4B0A, 0x4B8A, 0x4BEB)),
    ("Select", "Screens", SCREEN_PRINTER, (0x4C13,)),
    ("Ending", "Screens", SCREEN_PRINTER, (0x0D33,)),
    (
        "Banners",
        "Fight",
        FIGHT_PRINTER,
        (0x1F14, 0x1F27, 0x1F3D, 0x1F4A, 0x1F56, 0x1F70, 0x1F82, 0x1FBF),
    ),
    (
        "Bonuses",
        "Fight",
        FIGHT_PRINTER,
        (0x1FC9, 0x1FE4, 0x1FED, 0x2000, 0x2019, 0x2022, 0x203A, 0x205B, 0x2086),
    ),
    ("HUD line", "Fight", HUD_PRINTER, (0x19B8, 0x19FD)),
    ("Self test", "Debug", DEBUG_PRINTER, (0x0322, 0x0338)),
)
"""Every inline string, grouped by the screen that shows it. ``blocks``
asserts these are all the calls the ROM makes to the four printers."""

TABLE_OF = {
    SCREEN_PRINTER: "mk1",
    FIGHT_PRINTER: "mk1-fight",
    HUD_PRINTER: "mk1-hud",
    DEBUG_PRINTER: "mk1-debug",
}
FOLDERS = ("Screens", "Fight", "Debug")

INLINE_FILL = "fill=$00 end_is_fill=1"
"""What pads a shortened inline string: ``nop`` up to the code after it."""
INITIALS_FILL = "fill=$20"
"""What pads shorter initials: spaces, since all three are drawn."""

PICTURE_LOADS = (
    (0x4EF3, 0x4EFE, (0x4552, 0x4675, 0x4697, 0x4B04, 0x4B84, 0x4BE8, 0x4C10, 0x507F)),
    (0x4E81, 0x4E82, (0x4630,)),
)
"""The portraits' loader and Goro's picture's: the routine, its ``ld de,$8320``
and every call of it."""
PICTURE_SCREENS = {
    "Press start",
    "Credits",
    "Goro lives",
    "Biographies",
    "Link",
    "Select",
}
"""The groups printed over a picture at tile ``$32``: PRESS START blinks on
the credits screen (``1:$4552``), the biographies load portraits at
``1:$4675``/``$4697``, the link screens at ``$4B04``/``$4B84``/``$4BE8``,
select at ``$4C10``, and Goro's screen its picture at ``1:$4630``."""
PICTURE_TILES = {0x23: 0x32, 0x73: 0x33, 0x74: 0x34, 0x5F: 0x35}
"""The codes whose glyph the pictures overwrite, and the tile each draws."""

NOTES = {
    "Press start": {
        1: "Blanks the text above when it blinks: keep it covering the same cells."
    },
    "Title menu": {1: "The number of credits is drawn after it."},
    "Credits": {0: "The number of credits is drawn after it."},
    "Link": {
        1: "The player number is drawn after it.",
        2: "The player number is drawn after it.",
    },
    "Select": {0: "The player number is drawn after it."},
    "Banners": {
        None: "Each letter takes a tile from the fighters' 80 a frame; the longest "
        "banner draws 21.",
        1: "The round number is drawn after it.",
        7: "Drawn after the winner's name, the two centred as if this were 4 wide.",
    },
    "Bonuses": {
        None: "Each letter takes a tile from the fighters' 80 a frame; the longest "
        "banner draws 21.",
        0: "The bonus is drawn after it, then the next string.",
        3: "The bonus is drawn after it, then the next string.",
    },
    "HUD line": {
        None: "Clears the HUD line: keep 20 spaces, or the last fight's letters stay."
    },
    "Fighter names": {
        **dict.fromkeys(
            range(9),
            "1-8 letters: the HUD, the portrait and the winner's banner loop on the "
            "length, and an empty name runs 256 times.",
        ),
        **dict.fromkeys(range(9, 14), "Not read: fighter ids run 0-8."),
    },
    "Staff names": {None: "Not read: only while $D632 is set, which no code does."},
}
"""What a string's edit must keep that its table and slot cannot, by block
and string (``None``: every string of the block)."""


@dataclass(frozen=True)
class Block:
    name: str
    spec: str
    """The block's configuration line, as a native script's ``@block`` spells it."""
    folder: str | None = None
    """The Files panel folder the project puts the block in; ``None`` for none."""
    compression: tuple[str, int, int] | None = None
    """Always ``None``: no text is compressed."""
    notes: dict[int | None, str] | None = None
    """Notes the project puts on the block's strings, by index; ``None`` for
    every string."""


def _calls(rom: bytes, target: int) -> list[int]:
    """Every ``call target`` in banks 0 and 1, the two banks with code that
    reaches a printer."""
    pattern = bytes([0xCD, target & 0xFF, target >> 8])
    out, at = [], rom.find(pattern)
    while 0 <= at < 0x8000:
        out.append(at)
        at = rom.find(pattern, at + 1)
    return out


def inline_string(rom: bytes, site: int, printer: int) -> tuple[int, int]:
    """The ``(start, stop)`` of the string a ``call`` at ``site`` prints: from
    after the call to just past its ``00``, stepping over each code's operands."""
    operands = OPERANDS[printer]
    at = site + 3
    while rom[at]:
        at += 1 + operands.get(rom[at], 0)
    return site + 3, at + 1


def translation(rom: bytes) -> dict[int, int]:
    """``$2101``'s named characters: ``{code: tile}``, read off its compares."""
    start, stop = TRANSLATE
    out = {}
    for at in range(start, stop):
        if rom[at] == 0xFE and rom[at + 2] == 0xCA:
            dest = rom[at + 3] | rom[at + 4] << 8
            if rom[dest] == 0x3E and rom[dest + 2] == 0xC9:
                out[rom[at + 1]] = rom[dest + 1]
            elif rom[dest] == 0xAF and rom[dest + 1] == 0xC9:
                out[rom[at + 1]] = 0
    assert rom[0x212F:0x2133] == bytes([0xD6, 0x41, 0x3C, 0xC9]), "not $212F"
    assert rom[0x2133:0x2137] == bytes([0xD6, 0x30, 0xC6, DIGIT_BASE]), "not $2133"
    return out


def _header(table_id: str, comment: str) -> list[str]:
    lines = ["@mapchar table 1"]
    lines += [f"# {line}" if line else "#" for line in comment.split("\n")]
    return [*lines, f"@table {table_id}", ""]


LETTERS = range(0x41, 0x5B)
DIGITS = range(0x30, 0x3A)


def table_files(rom: bytes) -> dict[str, str]:
    """The sample's six native tables, by file name: one for each reading of
    a font the game draws text through, one for the screens a picture
    overwrites some of the font on, and one for the names both fonts draw."""
    named = translation(rom)
    main = {c: GLYPH[t] for c, t in named.items()}
    main |= {c: chr(c) for c in LETTERS}
    main |= {c: chr(c) for c in DIGITS}
    main |= UNIQUE_BY_ARITHMETIC
    assert all(main[c] == GLYPH[t] for c, t in PICTURE_TILES.items() if c in named)
    both = {c for c in range(0x20, 0x5B) if main.get(c) == chr(c)}
    files = {
        "mk1.tbl": [
            *_header(
                "mk1",
                "Mortal Kombat (Game Boy): the main font as the screen printer\n"
                "1:$4D94 reads it -- ASCII through $2101 to a glyph of the font\n"
                "at 1:$79F9. 02 draws a box: row, column, height, width.\n"
                "Generated from the ROM by tools/mk1_sample.py.",
            ),
            "/00=[end]",
            "$01{newline}=[pos],u8,u8",
            "$02=[box],u8,u8,u8,u8",
            *(f"{c:02X}={main[c]}" for c in sorted(main)),
        ],
        "mk1-fight.tbl": [
            *_header(
                "mk1-fight",
                "Mortal Kombat (Game Boy): the fight printer $20BC, which draws\n"
                "the main font's RAM copy at $D200 and has no box code.\n"
                "Generated from the ROM by tools/mk1_sample.py.",
            ),
            "@include mk1",
            "02=",
        ],
        "mk1-picture.tbl": [
            *_header(
                "mk1-picture",
                "Mortal Kombat (Game Boy): the screen printer over a picture at\n"
                "tile $32 (portraits, Goro), which draws picture tiles for the\n"
                "codes of TM, the two rules and _. Generated from the ROM by\n"
                "tools/mk1_sample.py.",
            ),
            "@include mk1",
            *(f"{c:02X}=" for c in sorted(PICTURE_TILES)),
        ],
        "mk1-names.tbl": [
            *_header(
                "mk1-names",
                "Mortal Kombat (Game Boy): a fighter name, drawn by the HUD font\n"
                "and by the main font through $2101 -- only what both draw\n"
                "alike. Generated from the ROM by tools/mk1_sample.py.",
            ),
            *(f"{c:02X}={chr(c)}" for c in sorted(both)),
        ],
        "mk1-hud.tbl": [
            *_header(
                "mk1-hud",
                "Mortal Kombat (Game Boy): the HUD font at 1:$7C13, glyph = code\n"
                "- $20, ASCII $20-$5A; the HUD line printer $1C20 ends a string at\n"
                "00. Generated from the ROM by tools/mk1_sample.py.",
            ),
            "/00=[end]",
            *(f"{c:02X}={chr(c)}" for c in range(0x20, 0x5B)),
        ],
        "mk1-debug.tbl": [
            *_header(
                "mk1-debug",
                "Mortal Kombat (Game Boy): the boot self-test printer $02F2,\n"
                "ASCII - $20, 01 moving to a row and column. Generated from the\n"
                "ROM by tools/mk1_sample.py.",
            ),
            "@charset ascii",
            "/00=[end]",
            "$01{newline}=[pos],u8,u8",
        ],
    }
    return {name: "\n".join(lines) + "\n" for name, lines in files.items()}


def _hex(n: int) -> str:
    return f"${n:X}"


def _inline(rom: bytes, name: str, folder: str, printer: int, sites) -> Block:
    """A range over the strings the ``sites`` print, skipping the code between,
    padded with ``nop``."""
    spans = [inline_string(rom, s, printer) for s in sites]
    skips = ",".join(
        f"{_hex(a[1])}>{_hex(b[0])}"
        for a, b in zip(spans, spans[1:], strict=False)
        if a[1] != b[0]
    )
    table = "mk1-picture" if name in PICTURE_SCREENS else TABLE_OF[printer]
    spec = (
        f"source=range start={_hex(spans[0][0])} stop={_hex(spans[-1][1])} "
        f"type=end table={table}"
    )
    spec += (f" skips={skips}" if skips else "") + " " + INLINE_FILL
    return Block(name, spec, folder, notes=NOTES.get(name))


def _names(rom: bytes, name: str, start: int, stop: int) -> Block:
    """Pascal names in 9-byte slots: realigning to the next slot after each
    name gives it the whole slot to grow into."""
    at = start
    while at < stop:
        at += max(NAME_SLOT, 1 + rom[at])
    assert at == stop, name
    return Block(
        name,
        f"source=range start={_hex(start)} stop={_hex(stop)} type=pascal:1 "
        f"table=mk1-names realign={NAME_SLOT}:{start % NAME_SLOT}",
        "Fight",
        notes=NOTES.get(name),
    )


def blocks(rom: bytes) -> list[Block]:
    """Every text block of the sample, in project order."""
    for printer in OPERANDS:
        grouped = sorted(s for _, _, p, sites in GROUPS if p == printer for s in sites)
        assert grouped == _calls(rom, printer), f"${printer:04X}'s calls"
    for routine, load, callers in PICTURE_LOADS:
        assert rom[load : load + 3] == bytes([0x11, 0x20, 0x83]), f"not ${load:04X}"
        assert tuple(_calls(rom, routine)) == callers, f"${routine:04X}'s calls"
    out = [_inline(rom, *group) for group in GROUPS]
    first, second, end = NAMES
    assert rom[NAME_LOOKUP : NAME_LOOKUP + 3] == bytes([0x01, first & 0xFF, first >> 8])
    out.append(_names(rom, "Fighter names", first, second))
    out.append(_names(rom, "Staff names", second, end))
    at, count, size, initials = SCORES
    assert rom[SCORE_COPY : SCORE_COPY + 3] == bytes([0x11, 0x3C, 0xD8]), "not $448A"
    out.append(
        Block(
            "High score initials",
            f"source=range start={_hex(at)} "
            f"stop={_hex(at + size * (count - 1) + initials)} "
            f"type=fixed:{initials} table=mk1 realign={size}:{at % size} "
            + INITIALS_FILL,
            "Screens",
        )
    )
    return sorted(out, key=lambda b: FOLDERS.index(b.folder or "Screens"))
