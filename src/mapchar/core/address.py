"""File-offset ⇄ display-address formats for navigation.

ROM documentation rarely gives positions as flat file offsets: it writes them as
``bank:address`` under the console's memory mapping, and a table or a pointer
list is nearly always quoted that way. Nearly every such mapping reduces to
three numbers — the bank size, the in-bank address the ROM window starts at, and
the number of the file's first bank — so a single parameterized
:class:`BankLayout` covers SNES LoROM/HiROM, GB banking, GBA and PC Engine.
Because SNES docs split between mirror spellings of the same byte (LoROM
``$00:8000`` vs ``$80:8000``, HiROM ``$C0`` vs the ``$40`` mirror SuperFX carts
standardize on), a layout can carry a second ``mirror`` anchor that parse folds
onto the first. The >4 MB SNES layouts (ExHiROM / ExLoROM) are piecewise — two
windows with different file bases — and get their own :class:`SplitBankLayout`.
:data:`BANK_PRESETS` names the common layouts; anything else is reachable
through the navigation bar's Custom bank fields.

Two deliberate limits of the single-mirror model: HiROM's *other* mirror — the
upper halves of the system banks (``$00–$3F``/``$80–$BF`` at ``$8000–$FFFF``) —
would need an anchor that also accepts the non-ROM lower halves, so strict parse
rejects that spelling; and the ``$00``/``$40``-anchored SNES presets format a
full 4 MB image's tail under banks ``$7E``/``$7F``, which the console overlays
with WRAM, a spelling parse accepts via the mirror. Qt-free, like all of
``core``.
"""

from __future__ import annotations

from dataclasses import dataclass

HEX_ID = "hex"
"""The flat-offset address format, which is not a bank layout and has no preset."""


def format_hex(offset: int) -> str:
    return f"{offset:06X}"


@dataclass(frozen=True)
class BankLayout:
    """A ``bank:address`` mapping described by three numbers.

    File offset ⇄ address: ``bank = bank_base + offset // bank_size`` and
    ``addr = addr_base + offset % bank_size``. Parse is strict — the in-bank
    address must fall inside the bank window (so e.g. LoROM ``$0000–$7FFF``,
    which is not ROM, is rejected) and the bank must be at or past ``bank_base``
    (or ``mirror``, when set). Format always uses ``bank_base``; ``mirror`` names
    the *other* anchor docs write the same bytes under, and parse folds it: the
    smallest non-negative bank-relative reading wins, so both spellings of a byte
    agree.
    """

    bank_size: int  # bytes of ROM per bank
    addr_base: int  # in-bank address of a bank's first byte
    bank_base: int  # bank number of the file's first byte
    mirror: int | None = None  # alternate first-bank anchor accepted on parse

    @property
    def addr_digits(self) -> int:
        """Hex digits of the largest in-bank address (min 4, the common width)."""
        bits = (self.addr_base + self.bank_size - 1).bit_length()
        return max(4, -(-bits // 4))

    def format(self, offset: int) -> str:
        bank, in_bank = divmod(offset, self.bank_size)
        addr = self.addr_base + in_bank
        return f"${self.bank_base + bank:02X}:{addr:0{self.addr_digits}X}"

    def parse(self, text: str) -> int | None:
        """``$BB:AAAA``, ``BB:AAAA`` or bare ``BBAAAA`` as a file offset.

        ``None`` for anything this layout cannot spell, so a committing input can
        revert rather than guess. In the bare form the last ``addr_digits``
        digits are the in-bank address — the convention six-digit console
        addresses are written in.
        """
        t = text.strip().lower().removeprefix("$").removeprefix("0x")
        if ":" in t:
            bank_s, _, addr_s = t.partition(":")
        else:
            bank_s, addr_s = t[: -self.addr_digits], t[-self.addr_digits :]
        try:
            bank, addr = int(bank_s, 16), int(addr_s, 16)
        except ValueError:
            return None
        if not (self.addr_base <= addr < self.addr_base + self.bank_size):
            return None
        anchors = (
            (self.bank_base,) if self.mirror is None else (self.bank_base, self.mirror)
        )
        relative = [bank - base for base in anchors if bank >= base]
        if not relative:
            return None
        return min(relative) * self.bank_size + (addr - self.addr_base)


@dataclass(frozen=True)
class SplitBankLayout:
    """A piecewise two-window mapping for >4 MB SNES carts (ExHiROM/ExLoROM).

    ``first`` maps file bytes below ``split`` (the classic 4 MB image) and
    ``second`` maps everything beyond, so bank→offset is two functions with
    different bases and does not reduce to one :class:`BankLayout`. The anchors
    are chosen so ``first`` spans exactly ``split`` bytes, which makes trying it
    before ``second`` on parse unambiguous.
    """

    first: BankLayout  # the window holding file bytes [0, split)
    second: BankLayout  # the window holding file bytes [split, ...)
    split: int  # file byte where the second window takes over

    def format(self, offset: int) -> str:
        if offset < self.split:
            return self.first.format(offset)
        return self.second.format(offset - self.split)

    def parse(self, text: str) -> int | None:
        offset = self.first.parse(text)
        if offset is not None and offset < self.split:
            return offset
        beyond = self.second.parse(text)
        return None if beyond is None else self.split + beyond


AddressLayout = BankLayout | SplitBankLayout
"""A way of spelling a file offset as ``bank:address``."""


@dataclass(frozen=True)
class BankPreset:
    id: str
    name: str
    layout: BankLayout | SplitBankLayout


# SNES notes: docs split about evenly between a mapping's two mirror spellings,
# so LoROM and HiROM each come in both display anchors, with the other anchor
# folded on parse. HiROM's $40 anchor is also the convention SuperFX carts use.
# GB note: the fixed home bank really sits at $0000–$3FFF, so this layout shows
# the first 16 KiB as $00:4000+ — the price of staying three-parameter. Every
# switchable bank, which is what docs cite, matches convention exactly.
BANK_PRESETS: tuple[BankPreset, ...] = (
    BankPreset(
        "snes-lorom",
        "SNES LoROM, banks $00–$7D",
        BankLayout(0x8000, 0x8000, 0x00, 0x80),
    ),
    BankPreset(
        "snes-lorom-80",
        "SNES LoROM, banks $80–$FF (FastROM)",
        BankLayout(0x8000, 0x8000, 0x80, 0x00),
    ),
    BankPreset(
        "snes-hirom",
        "SNES HiROM, banks $C0–$FF",
        BankLayout(0x10000, 0x0000, 0xC0, 0x40),
    ),
    BankPreset(
        "snes-hirom-40",
        "SNES HiROM, banks $40–$7D (Super FX)",
        BankLayout(0x10000, 0x0000, 0x40, 0xC0),
    ),
    BankPreset(
        "snes-exhirom",
        "SNES ExHiROM (>4 MB, $C0 then $40)",
        SplitBankLayout(
            BankLayout(0x10000, 0x0000, 0xC0),
            BankLayout(0x10000, 0x0000, 0x40),
            0x400000,
        ),
    ),
    BankPreset(
        "snes-exlorom",
        "SNES ExLoROM (>4 MB, $80 then $00)",
        SplitBankLayout(
            BankLayout(0x8000, 0x8000, 0x80),
            BankLayout(0x8000, 0x8000, 0x00),
            0x400000,
        ),
    ),
    BankPreset("gb", "GB banked", BankLayout(0x4000, 0x4000, 0x00)),
    BankPreset("gba", "GBA ROM", BankLayout(0x1000000, 0x000000, 0x08)),
    BankPreset("pce", "PCE HuCard", BankLayout(0x2000, 0x0000, 0x00)),
)

PRESETS_BY_ID: dict[str, BankPreset] = {p.id: p for p in BANK_PRESETS}


def format_address(offset: int, layout: AddressLayout | None = None) -> str:
    """``offset`` spelled under ``layout``, or as flat hex without one."""
    return format_hex(offset) if layout is None else layout.format(offset)


def parse_address(text: str, layout: AddressLayout | None = None) -> int | None:
    """Text as a file offset, or ``None`` for blank text or text nothing reads.

    ``layout``'s own spelling is tried first and a flat hex offset second, so a
    ``$C0:8000`` and a bare ``008000`` both land somewhere sensible under a
    HiROM mapping.
    """
    if layout is not None:
        offset = layout.parse(text)
        if offset is not None:
            return offset
    digits = text.strip().removeprefix("$").removeprefix("0x").removeprefix("0X")
    if not digits or not all(c in "0123456789abcdefABCDEF_" for c in digits):
        return None
    try:
        return int(digits, 16)
    except ValueError:
        return None
