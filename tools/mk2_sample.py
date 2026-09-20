"""The Mortal Kombat II sample: its tables and blocks, derived from the ROM itself.

Mortal Kombat II (USA, Europe) for the Game Boy (CRC32 ``BFAEADD0``, 256 KiB,
MBC1) keeps its text as ASCII in bank 2, which the code maps at ``$4000``: file
offset = CPU address + ``$4000``. No text has a symbol or a disassembly to
name it, so every block comes from the code that draws it -- the ``ld hl,nn``
before a call to one of the print routines -- and each character table from the
comparisons in the routine that draws it. Qt-free, so the tests use it too;
``make_sample_projects.py`` writes the tables and project.

What the ROM holds, and where each fact comes from:

- **Menu strings** end at ``00`` and are drawn by ``$1726`` (bank 2 paged in
  around ``$1734``): space, ``. , ! ?`` by compare, digits below ``$3A``, and
  letters above. Consecutive calls continue from where the last string ended,
  so one ``ld hl`` often reaches several strings; its operand is the block's
  pointer, and a list source over those operands lets a string grow.
- **Records** are ``[u16 screen offset][u8 length][characters]``, drawn one per
  call by ``$186E`` (which copies 32 bytes first) or ``$18DE`` (which leaves
  ``hl`` on the next record, so the story screens are chains of them). The
  offset is read by ``$1995`` and the characters by ``$19C8`` into ``$1930``,
  which draws space, ``!``, ``'``, digits and letters. A block over records
  steps over each header (``header=2``), which keeps the records in their
  slots: a chained one keeps its length, or the next header moves.
- **Winner messages** are records behind the 12 pointers at ``$457B``, which
  ``$0F83`` indexes by fighter; the block's offset lands on the length byte.
- **Fighter names** on the fight HUD (12 pointers at ``$4DE9``) are drawn by
  bank 2's ``$4DAC``, letters only, player 2's from the end back to the ``00``
  before it; the ``00`` at ``$4E01`` is the first name's.
- **Select-screen names** (8 pointers at ``$5863``, the 8 playable fighters)
  go through ``$1726``, space-padded to centre.
- **PRESS START** is 11 title-font tiles copied by ``$4104``, blinking against
  the 11 blank tiles after it. The title font's letters are ASCII + ``$90``
  and its space is ``01``, read off those two runs.
- **Not reachable**: the legal screen (``$B3F3``) and the credits (``$D255``)
  are tilemaps in two of the 29 ``RNC`` method-2 streams, which mapchar has no
  codec for. No other run of ASCII or tile-coded words is in the ROM.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

ROM_NAME = "Mortal Kombat II (USA, Europe).gb"

BANK2 = 0x4000
"""Bank 2 at CPU ``$4000`` is file ``$8000``: the file offset of a bank-2 address
is the address plus this."""

MENU_FONT = (0x1734, 0x1766)
"""``$1726``'s character loop: the compares that pick its punctuation."""
RECORD_FONT = (0x1930, 0x194C)
"""``$1915``'s character lookup, with the same kind of compares."""
DIGIT_BOUND = 0x3A
"""Both routines treat a byte below this as a digit, above it as a letter."""

TITLE_SITE = 0x8104
"""``ld hl,$411B``: PRESS START, 11 tiles."""
TITLE_WIDTH = 11
TITLE_OFFSET = 0x90
"""Title-font tile = ASCII + this, for letters."""
TITLE_SPACE = 0x01

MENUS = (
    # name, the ld hl sites that reach the strings, strings per site
    ("Title", (0x02DA,), 1),
    ("Main menu", (0x030F,), 2),
    ("Options menu", (0x0366,), 3),
    ("Arena", (0x0881,), 1),
    ("Continue", (0x08FD,), 2),
    ("Link", (0x0976, 0x097D, 0x0988, 0x0993, 0x099A), 1),
    ("Link lost", (0x09C6,), 2),
)
"""Menu strings reached by an ``ld hl,nn`` operand, each block its sites and how
many strings the calls after each read; every site is followed by that many
calls to ``$1726`` before ``hl`` is loaded again."""

MENU_TABLES = (
    # name, pointer table (CPU), count, the site that indexes it
    ("Difficulty", 0x4154, 3, 0x03C3),
    ("Arenas", 0x08B4, 2, 0x08A5),
)
"""Menu strings behind a pointer table, indexed by ``rst $08``."""

RECORDS = (
    # name, folder, the ld hl sites in order, records each site's calls read
    ("Round announcer", "Fight", ((0x0F4D, 5), (0x0F5F, 1), (0x0F67, 1))),
    (
        "Finishes",
        "Fight",
        (
            (0x0FB6, 1),
            (0x0FA7, 1),
            (0x0FBE, 1),
            (0x0FC3, 1),
            (0x0FCB, 1),
            (0x0FD3, 1),
            (0x0FDC, 1),
        ),
    ),
    ("Game over", "Fight", ((0x08D7, 1),)),
    ("Outworld", "Story", ((0x0FFE, 2),)),
    (
        "Goro's lair",
        "Story",
        ((0x1240, 1), (0x124B, 4), (0x1275, 4), (0x1289, 1), (0x1294, 2)),
    ),
    ("Ending", "Story", ((0x77C1, 2), (0x77CF, 4))),
)
"""Record runs, each a chain the sites' calls read in order: the round number
indexes ``$4533`` by 10, and the rest are one record a call. Bank 1's ending
(``$77AE``) pages bank 2 in through ``$0F83`` before it reads."""

WINNERS = (0x457B, 12, 0x0F77)
"""The winner messages' pointers, their count, and the site that indexes them."""
HUD_NAMES = (0x4DE9, 12, 0x8D9C)
SELECT_NAMES = (0x5863, 8, 0x089C)


@dataclass(frozen=True)
class Block:
    name: str
    spec: str
    """The block's configuration line, as a native script's ``@block`` spells it."""
    folder: str | None = None
    """The Files panel folder the project puts the block in; ``None`` for none."""


def _u16(rom: bytes, at: int) -> int:
    return struct.unpack_from("<H", rom, at)[0]


def _literal(rom: bytes, site: int) -> int:
    """The address an ``ld hl,nn`` at ``site`` loads."""
    assert rom[site] == 0x21, f"no ld hl at ${site:X}"
    return _u16(rom, site + 1)


def _compares(rom: bytes, span: tuple[int, int]) -> list[int]:
    """The ``cp n`` immediates in ``span``, the digit bound left out."""
    start, stop = span
    return [
        rom[i + 1]
        for i in range(start, stop)
        if rom[i] == 0xFE and rom[i + 1] != DIGIT_BOUND
    ]


def _header(table_id: str, comment: str) -> list[str]:
    lines = ["@mapchar table 1"]
    lines += [f"# {line}" if line else "#" for line in comment.split("\n")]
    return [*lines, f"@table {table_id}", ""]


def _ascii(codes: list[int]) -> list[str]:
    return [f"{c:02X}={chr(c)}" for c in codes]


DIGITS = list(range(0x30, DIGIT_BOUND))
LETTERS = list(range(0x41, 0x5B))


def table_files(rom: bytes) -> dict[str, str]:
    """The sample's four native tables, by file name: one for each routine
    that draws text, holding only what that routine can draw."""
    menu = sorted(_compares(rom, MENU_FONT))
    record = sorted(_compares(rom, RECORD_FONT))
    word = rom[TITLE_SITE + 1 : TITLE_SITE + 3]
    press = BANK2 + _u16(word, 0)
    assert bytes(rom[press : press + TITLE_WIDTH]) == bytes(
        TITLE_SPACE if c == " " else ord(c) + TITLE_OFFSET for c in "PRESS START"
    )
    files = {
        "mk2.tbl": [
            *_header(
                "mk2",
                "Mortal Kombat II (Game Boy): the menu font $1726 draws, ASCII, each\n"
                "string ended by 00. Generated from the ROM by tools/mk2_sample.py.",
            ),
            "/00=[end]",
            *_ascii(sorted(menu + DIGITS + LETTERS)),
        ],
        "mk2-records.tbl": [
            *_header(
                "mk2-records",
                "Mortal Kombat II (Game Boy): the record font $19C8 draws, ASCII,\n"
                "each record counted by its length byte. Generated from the ROM by\n"
                "tools/mk2_sample.py.",
            ),
            *_ascii(sorted(record + DIGITS + LETTERS)),
        ],
        "mk2-names.tbl": [
            *_header(
                "mk2-names",
                "Mortal Kombat II (Game Boy): the fight HUD's names, letters only,\n"
                "drawn by $4DAC. Generated from the ROM by tools/mk2_sample.py.",
            ),
            "/00=[end]",
            *_ascii(LETTERS),
        ],
        "mk2-title.tbl": [
            *_header(
                "mk2-title",
                "Mortal Kombat II (Game Boy): the title screen's tiles, letters at\n"
                "ASCII + $90 and a blank at 01. Generated from the ROM by\n"
                "tools/mk2_sample.py.",
            ),
            f"{TITLE_SPACE:02X}= ",
            *(f"{c + TITLE_OFFSET:02X}={chr(c)}" for c in LETTERS),
        ],
    }
    return {name: "\n".join(lines) + "\n" for name, lines in files.items()}


def _end(rom: bytes, at: int) -> int:
    """Just past the ``00`` that ends the string at ``at``."""
    return rom.index(0, at) + 1


def _record_end(rom: bytes, at: int) -> int:
    """Just past the record whose header is at ``at``."""
    return at + 3 + rom[at + 2]


def _pointers(
    name: str, table: int, count: int, offset: int, rest: str, folder: str
) -> Block:
    start = table + BANK2 if table >= 0x4000 else table
    return Block(
        name,
        f"source=pointers start=${start:X} stop=${start + 2 * count:X} size=2 "
        f"stride=2 endian=little mapping=linear offset={offset} bank=0 {rest}",
        folder,
    )


def _menu(rom: bytes, name: str, sites: tuple[int, ...], per: int) -> Block:
    """A list source over the operands of ``sites``: each reaches ``per``
    strings, which the packed layout keeps back to back."""
    for site in sites:
        at = BANK2 + _literal(rom, site)
        for _ in range(per):
            at = _end(rom, at)
    addresses = ",".join(f"${site + 1:X}" for site in sites)
    return Block(
        name,
        f"source=list addresses={addresses} size=2 endian=little "
        f"mapping=linear offset={BANK2} bank=0 type=end table=mk2 spp={per} "
        "mode=packed",
        "Menus",
    )


def _records(rom: bytes, name: str, folder: str, sites) -> Block:
    """A range over a chain of records, each behind its two-byte header."""
    first = at = None
    for site, count in sites:
        start = BANK2 + _literal(rom, site)
        assert at is None or start == at, (name, hex(site))
        at = start
        first = start if first is None else first
        for _ in range(count):
            at = _record_end(rom, at)
    return Block(
        name,
        f"source=range start=${first:X} stop=${at:X} type=pascal:1 "
        "table=mk2-records header=2",
        folder,
    )


def blocks(rom: bytes) -> list[Block]:
    """Every text block of the sample, in project order."""
    title = BANK2 + _literal(rom, TITLE_SITE)
    out = [
        Block(
            "Press start",
            f"source=range start=${title:X} stop=${title + TITLE_WIDTH:X} "
            f"type=fixed:{TITLE_WIDTH} table=mk2-title",
            "Menus",
        )
    ]
    for name, sites, per in MENUS:
        out.append(_menu(rom, name, sites, per))
    for name, table, count, site in MENU_TABLES:
        assert _literal(rom, site) == table, name
        stop = max(
            _end(rom, BANK2 + _u16(rom, _file(table) + 2 * i)) for i in range(count)
        )
        out.append(
            _pointers(
                name,
                table,
                count,
                BANK2,
                f"type=end table=mk2 bound=${stop:X}",
                "Menus",
            )
        )
    for name, folder, sites in RECORDS:
        out.append(_records(rom, name, folder, sites))
    table, count, site = WINNERS
    assert _literal(rom, site) == table
    out.append(
        _pointers(
            "Winners",
            table,
            count,
            BANK2 + 2,
            "type=pascal:1 table=mk2-records mode=slotted",
            "Fight",
        )
    )
    for name, (table, count, site), table_id in (
        ("Fighter names", HUD_NAMES, "mk2-names"),
        ("Select names", SELECT_NAMES, "mk2"),
    ):
        assert _literal(rom, site) == table, name
        last = BANK2 + _u16(rom, _file(table) + 2 * (count - 1))
        out.append(
            _pointers(
                name,
                table,
                count,
                BANK2,
                f"type=end table={table_id} bound=${_end(rom, last):X}",
                "Fight",
            )
        )
    order = ["Menus", "Fight", "Story"]
    return sorted(out, key=lambda b: order.index(b.folder or "Menus"))


def _file(address: int) -> int:
    """The file offset of a CPU address: bank 0 as it is, bank 2 moved up."""
    return address + BANK2 if address >= 0x4000 else address
