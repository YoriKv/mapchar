"""Tables, entries and table sets: the model behind every table file.

A ``Table`` is a set of entries keyed by the bits they match. A ``TableSet``
is the start table plus every table its switch entries can reach; the decode
and encode engines run over a table set, never over a file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from mapchar.core.bits import bits_to_hex, hex_to_bits
from mapchar.core.errors import TableError
from mapchar.core.numbers import parse_num
from mapchar.core.text import nfc

RAW = "raw"
"""Pseudo table id: one unmatched byte per match, shown ``[$XX]``."""
BITS = "bits"
"""Pseudo table id: one unmatched bit per match, shown ``[%b]``."""
RETURN = "return"
"""Pseudo destination: once the parameters before it are done, leave the
frame of the table the switch was matched in (at the top level, end the
string). Always the last parameter."""

ID_PATTERN = re.compile(r"[\w.-]+")
"""A table id: letters and digits in any script, ``_``, ``.`` and ``-``, so
``@table かんじ`` names a table after what is in it. No whitespace."""
LABEL_PATTERN = re.compile(r"[^\[\]\s$%][^\[\]\s]*")
BRACKETED = re.compile(r"\[(" + LABEL_PATTERN.pattern + r")\](?:\\n)*")
"""A text that is exactly one code, optionally followed by line-break escapes."""


class TokenKind(Enum):
    TEXT = "text"
    END = "end"
    CODE = "code"
    SWITCH = "switch"
    RETURN = "return"


@dataclass(frozen=True)
class OperandSpec:
    """One operand of a code entry: what to read and how to show it."""

    kind: str
    """``uint``, ``sint``, ``bytes`` or ``bits``."""
    bits: int
    big_endian: bool = False

    _SPECS = {
        "u8": ("uint", 8, False),
        "u16": ("uint", 16, False),
        "u24": ("uint", 24, False),
        "u32": ("uint", 32, False),
        "u16be": ("uint", 16, True),
        "u24be": ("uint", 24, True),
        "u32be": ("uint", 32, True),
        "s8": ("sint", 8, False),
        "s16": ("sint", 16, False),
        "s16be": ("sint", 16, True),
    }

    @classmethod
    def parse(cls, spec: str) -> OperandSpec:
        spec = spec.strip()
        if spec in cls._SPECS:
            return cls(*cls._SPECS[spec])
        if spec.isdigit() and int(spec) > 0:
            return cls("bytes", int(spec) * 8)
        if spec.startswith("bits:") and spec[5:].isdigit() and int(spec[5:]) > 0:
            return cls("bits", int(spec[5:]))
        raise ValueError(f"unknown operand spec {spec!r}")

    def spec(self) -> str:
        for name, tup in self._SPECS.items():
            if tup == (self.kind, self.bits, self.big_endian):
                return name
        if self.kind == "bytes":
            return str(self.bits // 8)
        return f"bits:{self.bits}"

    def value_of(self, bits: str) -> int:
        """Interpret ``bits`` (exactly ``self.bits`` long) as this operand."""
        if self.kind == "bits":
            return int(bits, 2)
        data = int(bits, 2).to_bytes(self.bits // 8, "big")
        if self.kind == "bytes":
            return int.from_bytes(data, "big")
        order = "big" if self.big_endian else "little"
        return int.from_bytes(data, order, signed=self.kind == "sint")

    def bits_of(self, value: int) -> str:
        """The inverse of :meth:`value_of`."""
        if self.kind == "bits":
            return format(value & ((1 << self.bits) - 1), f"0{self.bits}b")
        n = self.bits // 8
        if self.kind == "bytes":
            data = value.to_bytes(n, "big")
        else:
            order = "big" if self.big_endian else "little"
            data = value.to_bytes(n, order, signed=self.kind == "sint")
        return format(int.from_bytes(data, "big"), f"0{self.bits}b")

    def render(self, value: int) -> str:
        if self.kind == "bits":
            return "%" + format(value, f"0{self.bits}b")
        if self.kind == "sint":
            return str(value)
        if self.kind == "bytes":
            n = self.bits // 8
            data = value.to_bytes(n, "big")
            return " ".join(f"${b:02X}" for b in data)
        return f"${value:0{self.bits // 4}X}"

    def parse_value(self, word: str) -> int:
        if self.kind == "bits":
            if not word.startswith("%"):
                raise ValueError(word)
            return int(word[1:], 2)
        return parse_num(word)

    @property
    def words(self) -> int:
        """How many rendered words this operand occupies."""
        return self.bits // 8 if self.kind == "bytes" else 1


COUNT_SPECS = ("u8", "u16", "u24", "u32", "u16be", "u24be", "u32be")
"""The operands a switch parameter may read its count from: the unsigned ones."""


@dataclass(frozen=True)
class Stop:
    """When a switch frame pops."""

    count: int | None = None
    """Exactly this many weighted matches."""
    fallback: str | None = None
    """Bits that end the frame when they appear at a token boundary."""
    operand: OperandSpec | None = None
    """The count is read from the data as this operand when the frame opens:
    a Pascal string, or a code followed by as many bytes as its next byte
    says. Consumed and shown as nothing, like fallback bits."""

    @property
    def any(self) -> bool:
        return self.count is None and self.fallback is None and self.operand is None

    def spec(self, any_marker: str = "*") -> str:
        """How a switch parameter writes this stop; ``any_marker`` for no stop."""
        if self.count is not None:
            return str(self.count)
        if self.operand is not None:
            return self.operand.spec()
        if self.fallback is not None:
            if len(self.fallback) % 8 == 0:
                return "$" + bits_to_hex(self.fallback)
            return "%" + self.fallback
        return any_marker


def parse_stop(word: str) -> Stop:
    """A switch parameter's stop as the table dialects write it.

    ``*`` and ``0`` are no stop at all, digits a weighted count, an unsigned
    operand spec a count read from the data, ``$hex`` and ``%bits`` fallback
    bits.
    """
    if word in ("*", "0"):
        return Stop()
    if word.isdigit():
        return Stop(count=int(word))
    if word in COUNT_SPECS:
        return Stop(operand=OperandSpec.parse(word))
    if word.startswith("$"):
        return Stop(fallback=hex_to_bits(word[1:]))
    return Stop(fallback=word[1:])


@dataclass(frozen=True)
class SwitchParam:
    table_id: str
    stop: Stop = Stop()
    shared: bool = False

    def spec(self) -> str:
        if self.table_id == RETURN:
            return RETURN
        return f"@{self.table_id}:{self.stop.spec()}{'+' if self.shared else ''}"


@dataclass(frozen=True)
class Entry:
    bits: str
    kind: TokenKind
    text: str = ""
    """TEXT, END and SWITCH: the text in script form (escapes intact, ``\\n``
    as two characters); a SWITCH with empty text is silent. CODE: the label.
    RETURN: empty."""
    weight: int = 1
    operands: tuple[OperandSpec, ...] = ()
    params: tuple[SwitchParam, ...] = ()
    comment: str = ""
    """The comment lines directly above the entry in its file, without their
    ``#``, joined by newlines. Written back above it."""

    def __post_init__(self) -> None:
        # Every entry's text is NFC, whatever form the file it came from used,
        # so a table compares and encodes the same however it was typed.
        composed = nfc(self.text)
        if composed != self.text:
            object.__setattr__(self, "text", composed)

    @property
    def label(self) -> str | None:
        """The code label this entry answers to, if any.

        CODE and SWITCH entries always have one. A TEXT or END entry whose
        text is exactly ``[label]`` is a labelled code too, so ``/FF=[end]``
        dumps and re-inserts as ``[end]``.
        """
        if self.kind is TokenKind.CODE:
            return self.text
        if self.kind in (TokenKind.TEXT, TokenKind.END, TokenKind.SWITCH):
            m = BRACKETED.fullmatch(self.text)
            if m:
                return m.group(1)
        return None

    @property
    def silent(self) -> bool:
        """A switch that prints nothing; the encoder inserts it where needed."""
        return self.kind is TokenKind.SWITCH and self.text == ""

    @property
    def is_end(self) -> bool:
        return self.kind is TokenKind.END


def sanitize_id(text: str) -> str:
    """``text`` as a table id: everything ``ID_PATTERN`` rejects becomes ``_``.

    Letters of every script pass, so two Japanese-named tables in one legacy
    file keep their own names instead of colliding on underscores.
    """
    return re.sub(r"[^\w.-]", "_", nfc(text))


def sanitize_label(text: str) -> str:
    """``text`` as a code label: an outer bracket pair off, no whitespace."""
    text = text.strip()
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        text = text[1:-1]
    fixed = re.sub(r"\s+", "_", text.strip())
    fixed = re.sub(r"[\[\]]", "_", fixed)
    if not fixed or fixed[0] in "$%":
        fixed = "_" + fixed
    return fixed if LABEL_PATTERN.fullmatch(fixed) else "_" + re.sub(r"\W", "_", fixed)


class Table:
    """One logical table: entries keyed by bits, plus derived lookups."""

    def __init__(self, id: str, charset: str = "none"):
        id = nfc(id)
        if not ID_PATTERN.fullmatch(id):
            raise TableError(f"invalid table id {id!r}")
        self.id = id
        self.charset = charset
        self.comment = ""
        """The file's own comment lines: every one not directly above an entry."""
        self.entries: dict[str, Entry] = {}
        self.charset_entries: dict[str, Entry] = {}
        """Every code the charset contributes, whether or not the file overrides
        it, so a write can leave out what the charset already says."""
        self.labels: dict[str, Entry] = {}
        self.aliases: dict[str, str] = {}
        """Extra script-form text the encoder accepts for an entry's bits.

        A charset that folds several characters onto one code puts the ones
        that do not decode back here (Shift-JIS ``¥`` at ``5C``): typing them
        encodes, while the code still decodes as the entry's own text."""
        self.charset_applied = False
        """Whether the table's charset has already been folded in.

        :func:`~mapchar.plugins.charsets.apply_charset` is reached from every
        read and every reload, and folding a charset in twice would re-add the
        entries a table removed. Cleared to re-apply after the plugins change.
        """
        self._by_length: dict[int, dict[str, Entry]] = {}
        self._lengths: tuple[int, ...] = ()

    def __repr__(self) -> str:
        return f"Table({self.id!r}, {len(self.entries)} entries)"

    def add(self, entry: Entry, *, replace: bool = False) -> None:
        """Add an entry. Duplicate bits or labels are an error unless ``replace``."""
        if entry.bits in self.entries and not replace:
            raise TableError(f"duplicate key {entry.bits!r} in table {self.id!r}")
        label = entry.label
        if label is not None:
            other = self.labels.get(label)
            if other is not None and other.bits != entry.bits and not replace:
                raise TableError(f"duplicate label [{label}] in table {self.id!r}")
        old = self.entries.get(entry.bits)
        if old is not None:
            self._remove(old)
        self.entries[entry.bits] = entry
        if label is not None:
            self.labels[label] = entry
        self._by_length.setdefault(len(entry.bits), {})[entry.bits] = entry
        self._lengths = tuple(sorted(self._by_length, reverse=True))

    def remove(self, bits: str) -> None:
        entry = self.entries.get(bits)
        if entry is not None:
            self._remove(entry)

    def _remove(self, entry: Entry) -> None:
        del self.entries[entry.bits]
        if entry.label is not None and self.labels.get(entry.label) is entry:
            del self.labels[entry.label]
        group = self._by_length[len(entry.bits)]
        del group[entry.bits]
        if not group:
            del self._by_length[len(entry.bits)]
            self._lengths = tuple(sorted(self._by_length, reverse=True))

    @property
    def max_bits(self) -> int:
        return self._lengths[0] if self._lengths else 0

    def match(self, window: str) -> Entry | None:
        """The longest entry that prefixes ``window``."""
        for length in self._lengths:
            if length > len(window):
                continue
            entry = self._by_length[length].get(window[:length])
            if entry is not None:
                return entry
        return None

    def add_alias(self, text: str, bits: str) -> None:
        """Let script-form ``text`` encode as ``bits``, which an entry holds."""
        text = nfc(text)
        if text and bits in self.entries and text != self.entries[bits].text:
            self.aliases[text] = bits

    def switch_targets(self) -> set[str]:
        ids: set[str] = set()
        for entry in self.entries.values():
            for param in entry.params:
                if param.table_id not in (RAW, BITS, RETURN):
                    ids.add(param.table_id)
        return ids

    def sorted_entries(self) -> list[Entry]:
        return sorted(self.entries.values(), key=lambda e: (len(e.bits), e.bits))

    def own_entries(self) -> list[Entry]:
        """What a file has to say beyond the charset: every entry the charset
        does not already give, plus an empty-text entry for each charset code
        the table has dropped, so the file reads back to this table."""
        own = [
            e for e in self.sorted_entries() if self.charset_entries.get(e.bits) != e
        ]
        dropped = [
            Entry(bits, TokenKind.TEXT, "")
            for bits in self.charset_entries
            if bits not in self.entries
        ]
        return sorted(own + dropped, key=lambda e: (len(e.bits), e.bits))

    def replace_with(self, other: Table) -> None:
        """Become ``other``, keeping this object's identity.

        The views and the Table Editor hold one ``Table``, so an undo or a
        change of charset puts new contents into it rather than swapping it out.
        """
        self.id = other.id
        self.charset = other.charset
        self.comment = other.comment
        self.charset_entries = dict(other.charset_entries)
        self.charset_applied = other.charset_applied
        self.aliases = dict(other.aliases)
        for bits in list(self.entries):
            self.remove(bits)
        for entry in other.entries.values():
            self.add(entry)


@dataclass
class TableSet:
    """The start table plus every table reachable from it by switches."""

    start: Table
    tables: dict[str, Table] = field(default_factory=dict)

    @classmethod
    def build(cls, start: Table, available: dict[str, Table]) -> TableSet:
        """Close over switch targets, failing on one that is not ``available``."""
        tables = {start.id: start}
        pending = [start]
        while pending:
            table = pending.pop()
            for target in table.switch_targets():
                if target in tables:
                    continue
                if target not in available:
                    raise TableError(
                        f"table {table.id!r} switches to unknown table {target!r}"
                    )
                tables[target] = available[target]
                pending.append(available[target])
        return cls(start, tables)

    def table(self, id: str) -> Table:
        return self.tables[id]

    def entry_for_label(self, label: str) -> list[tuple[Table, Entry]]:
        return [(t, t.labels[label]) for t in self.tables.values() if label in t.labels]
