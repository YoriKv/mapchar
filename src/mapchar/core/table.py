"""Tables, entries and table sets: the model behind every table file.

A ``Table`` is a set of entries keyed by the bits they match. A ``TableSet``
is the start table plus every table its switch entries can reach; the decode
and encode engines run over a table set, never over a file.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from mapchar.core.bits import bits_to_hex
from mapchar.core.errors import TableError
from mapchar.core.font import Effect
from mapchar.core.numbers import parse_num
from mapchar.core.text import nfc

_T = TypeVar("_T")

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
TABLE_EFFECTS = (Effect.NEWLINE, Effect.PAGE, Effect.PAUSE)
"""The layout effects a table entry can declare: what a code does to the text
box whatever font draws it. The rest (a space, a glyph, an end of drawing) are
a block's box's to say."""


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


@dataclass(frozen=True)
class SwitchParam:
    table_id: str
    stop: Stop = Stop()
    shared: bool = False
    through: bool = False
    """The frame falls through: bits its table does not match are matched in
    the frame beneath, and on down while that one falls through too."""

    def spec(self) -> str:
        if self.table_id == RETURN:
            return RETURN
        marks = ("+" if self.shared else "") + ("|" if self.through else "")
        return f"@{self.table_id}:{self.stop.spec()}{marks}"


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
    effect: Effect = Effect.NONE
    """What the entry does to layout (:data:`TABLE_EFFECTS`): a *newline* or
    *page* breaks the line wherever the text is shown, a *pause* is only known."""

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

    def is_newline(self, label: str) -> bool:
        """Whether this entry is a line code: one that declares the *newline*
        effect, or the block's line code ``[label]`` — a code with that label,
        or text that ends in it. Every rendering breaks the line after such a
        token, without a ``\\n`` in its text."""
        if self.effect is Effect.NEWLINE:
            return True
        if not label:
            return False
        if self.kind is TokenKind.CODE:
            return self.text == label
        if self.kind in (TokenKind.TEXT, TokenKind.END, TokenKind.SWITCH):
            return _line_code(label).search(self.text) is not None
        return False


@functools.lru_cache(maxsize=16)
def _line_code(label: str) -> re.Pattern[str]:
    """``[label]`` at the end of a text, line-break escapes aside."""
    return re.compile(r"\[" + re.escape(label) + r"\](?:\\n)*$")


class Table:
    """One logical table: entries keyed by bits, plus derived lookups.

    ``entries`` is what the table's own file says over its charset. The tables
    it includes (``includes``) are not folded in: :func:`resolve` lays them
    under it when a :class:`TableSet` is built, so an included table edited in
    the app reaches every table that includes it.
    """

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
        self.revision = 0
        """Bumped by every change of entries, aliases or includes: what the
        lookups derived from the table are cached against."""
        self._includes: tuple[str, ...] = ()
        self._by_length: dict[int, dict[str, Entry]] = {}
        self._lengths: tuple[int, ...] = ()
        self._cache: dict[str, tuple[Any, Any]] = {}

    def __repr__(self) -> str:
        return f"Table({self.id!r}, {len(self.entries)} entries)"

    def __getstate__(self) -> dict:
        # A copy (an undo snapshot) leaves the derived caches behind: a resolved
        # table is as big as everything it includes.
        state = dict(self.__dict__)
        state["_cache"] = {}
        return state

    def __deepcopy__(self, memo: dict) -> Table:
        """A copy that shares its entries and copies the lookups over them.

        An :class:`Entry` is frozen and is never edited in place — a change
        makes another one — so a snapshot need not copy tens of thousands of
        them to be independent of the table it was taken from. The dicts that
        hold them are copied, which is what tells the two tables apart.
        """
        other = Table.__new__(Table)
        memo[id(self)] = other
        other.__dict__.update(self.__getstate__())
        other.entries = dict(self.entries)
        other.charset_entries = dict(self.charset_entries)
        other.labels = dict(self.labels)
        other.aliases = dict(self.aliases)
        other._by_length = {n: dict(g) for n, g in self._by_length.items()}
        return other

    @property
    def includes(self) -> tuple[str, ...]:
        """The ids of the tables whose entries this one starts from, in order
        (``@include``): each lays its entries over the one before, and the
        table's own entries go over them all."""
        return self._includes

    @includes.setter
    def includes(self, ids) -> None:
        self._includes = tuple(ids)
        self.revision += 1

    def cached(self, name: str, make: Callable[[], _T], key: Any = None) -> _T:
        """``make()``, remembered until the table changes.

        Public because the lookups derived from a table are not all its own:
        the encoder's index over its entries is built the same way and kept
        here too, so it outlives the one call that needed it. ``key`` is what
        the slot is remembered against, the table's own revision unless a
        caller gives one — an include resolution turns over when any table it
        reaches does, so it passes a key over all of them.
        """
        if key is None:
            key = self.revision
        hit = self._cache.get(name)
        if hit is not None and hit[0] == key:
            return hit[1]
        value = make()
        self._cache[name] = (key, value)
        return value

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
        self.revision += 1

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
        self.revision += 1

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
            self.revision += 1

    def switch_targets(self) -> set[str]:
        return set(self.cached("targets", self._switch_targets))

    def _switch_targets(self) -> frozenset[str]:
        ids: set[str] = set()
        for entry in self.entries.values():
            for param in entry.params:
                if param.table_id not in (RAW, BITS, RETURN):
                    ids.add(param.table_id)
        return frozenset(ids)

    def has_switch(self) -> bool:
        """Whether any entry switches table, so the decoder carries state
        across a token and no byte in the middle of a decode is a fresh start."""
        return self.cached(
            "has_switch",
            lambda: any(e.kind is TokenKind.SWITCH for e in self.entries.values()),
        )

    def key_span(self) -> int:
        """The most bits one token of this table reads: its longest key with
        that entry's operands."""
        return self.cached(
            "key_span",
            lambda: max(
                (
                    len(e.bits) + sum(o.bits for o in e.operands)
                    for e in self.entries.values()
                ),
                default=8,
            ),
        )

    def effects(self) -> dict[str, Effect]:
        """Every label whose entry declares a layout effect, with the effect."""
        return self.cached(
            "effects",
            lambda: {
                label: e.effect
                for label, e in self.labels.items()
                if e.effect is not Effect.NONE
            },
        )

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
        self.includes = other.includes
        for bits in list(self.entries):
            self.remove(bits)
        for entry in other.entries.values():
            self.add(entry)


@dataclass
class TableSet:
    """The start table plus every table reachable from it by switches, each
    resolved over what it includes
    (:func:`~mapchar.core.table_layers.resolve`)."""

    start: Table
    tables: dict[str, Table] = field(default_factory=dict)

    @classmethod
    def build(cls, start: Table, available: Mapping[str, Table]) -> TableSet:
        """Close over switch targets, failing on one that is not ``available``
        and on an include that does not resolve."""
        # Here rather than at the top: the layering is written over this module.
        from mapchar.core.table_layers import resolve

        start = resolve(start, available)
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
                resolved = resolve(available[target], available)
                tables[target] = resolved
                pending.append(resolved)
        return cls(start, tables)

    def table(self, id: str) -> Table:
        return self.tables[id]

    def entry_for_label(self, label: str) -> list[tuple[Table, Entry]]:
        return [(t, t.labels[label]) for t in self.tables.values() if label in t.labels]
