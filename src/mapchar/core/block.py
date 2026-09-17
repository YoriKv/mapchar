"""Blocks and strings: how a region of a file is cut into editable units."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from mapchar.core.notices import Notice
from mapchar.core.text import nfc, same_text
from mapchar.core.tokens import Token


@dataclass(frozen=True)
class RangeSource:
    """Consecutive strings from ``start`` to ``stop`` (exclusive), in bytes."""

    start: int
    stop: int


@dataclass(frozen=True)
class PointerRef:
    address: int
    size: int
    endian: str
    mapping_id: str
    offset: int
    """Added to the mapped value: the source's offset, or for a nested source's
    inner pointer the base its group counts from."""
    value: int


@dataclass(frozen=True)
class PointerTableSource:
    start: int
    stop: int
    size: int
    stride: int
    endian: str = "little"
    mapping_id: str = "linear"
    offset: int = 0
    bank: int = 0
    null: int | None = None
    """A raw pointer value that means "no string": not read, never rewritten."""


@dataclass(frozen=True)
class PointerListSource:
    addresses: tuple[int, ...]
    size: int
    endian: str = "little"
    mapping_id: str = "linear"
    offset: int = 0
    bank: int = 0
    null: int | None = None


@dataclass(frozen=True)
class NestedPointerSource:
    """A pointer table whose records each give an inner pointer table and the
    base its pointers count from.

    The outer table runs from ``start`` to ``stop`` by ``stride``, read as a
    pointer table is (``size``, ``endian``, ``mapping_id``, ``offset``,
    ``bank``). A record holds two outer pointers: the inner table at record
    offset 0 and its base at record offset ``size``. The inner table runs from
    its own address up to the base, ``inner_size`` bytes a pointer, and each
    inner pointer's target is its value plus the base. A record whose outer
    pointers either hold ``null`` is skipped, as is an inner pointer holding
    ``inner_null``. The strings one record's table reaches are its **group**,
    laid out on their own (:func:`string_groups`).
    """

    start: int
    stop: int
    size: int
    stride: int
    endian: str = "little"
    mapping_id: str = "linear"
    offset: int = 0
    bank: int = 0
    inner_size: int = 2
    inner_endian: str = "little"
    null: int | None = None
    inner_null: int | None = None


Source = RangeSource | PointerTableSource | PointerListSource | NestedPointerSource
PointerSource = PointerTableSource | PointerListSource | NestedPointerSource
"""The sources that read strings at pointer targets."""


def source_start(source: Source | None) -> int | None:
    """Where a source begins in the file, or ``None`` when it does not say.

    Every source but one carries its own ``start``; a pointer list begins at its
    lowest pointer address instead, and an empty one — like no source at all —
    says nothing, which leaves the caller free to fall back on where the strings
    actually landed.
    """
    if source is None:
        return None
    if isinstance(source, PointerListSource):
        return min(source.addresses) if source.addresses else None
    return source.start


def source_span(source: Source | None) -> tuple[int, int] | None:
    """The bytes a source itself occupies, as ``(start, stop)``, or ``None``.

    The range read for a range source, the table for a pointer table,
    and for a pointer list the stretch from its lowest pointer to the end of its
    highest — the pointers, never the strings they reach. An empty source says
    nothing.
    """
    if source is None:
        return None
    if isinstance(source, PointerListSource):
        if not source.addresses:
            return None
        span = (min(source.addresses), max(source.addresses) + source.size)
    else:
        span = (source.start, source.stop)
    return span if span[1] > span[0] else None


@dataclass(frozen=True)
class EndToken:
    """The string ends at the first end token."""


@dataclass(frozen=True)
class FixedLength:
    length: int
    stop_at_end: bool = False


@dataclass(frozen=True)
class Pascal:
    width: int = 1
    counts_tokens: bool = False
    endian: str = "little"


@dataclass(frozen=True)
class NextPointer:
    """The string ends at the next pointer's target."""


@dataclass(frozen=True)
class Lines:
    """The string ends after ``count`` line codes, or at an end token."""

    count: int = 1


StringType = EndToken | FixedLength | Pascal | NextPointer | Lines


class WriteMode(Enum):
    PACKED = "packed"
    SLOTTED = "slotted"


@dataclass(frozen=True)
class BlockConfig:
    source: Source
    string_type: StringType = EndToken()
    table_id: str = ""
    strings_per_pointer: int = 1
    realign: tuple[int, int] = (0, 0)
    """``(multiple, offset)`` in bytes; a multiple of 0 disables."""
    skips: tuple[tuple[int, int], ...] = ()
    """``(from, to)`` byte pairs: reading ``from`` continues at ``to``."""
    line_length: int = 0
    """For fixed strings, split each into lines this long (0: off)."""
    bound: int | None = None
    """Exclusive end address strings may not cross on write."""
    write_mode: WriteMode | None = None
    """``None`` picks packed with pointers and slotted without."""
    fill: bytes = b"\xff"
    """The pattern that pads unused room, repeated from the start of the room
    it fills (:func:`fill_run`)."""
    show_end: bool = False
    """Append an artificial ``[end]`` code to every fixed string."""
    end_label: str = "end"
    """Label of the artificial end code shown after fixed strings."""
    line_label: str = "line"
    """Label of the artificial line code shown between fixed lines."""

    @property
    def has_pointers(self) -> bool:
        return isinstance(self.source, PointerSource)

    @property
    def fixed_length(self) -> int | None:
        """The byte length every string has, when they all have one."""
        if isinstance(self.string_type, FixedLength):
            return self.string_type.length
        return None

    @property
    def effective_write_mode(self) -> WriteMode:
        if self.write_mode is not None:
            return self.write_mode
        return default_write_mode(self.has_pointers, bool(self.skips))


def default_write_mode(pointers: bool, skips: bool) -> WriteMode:
    """The write mode a block without one of its own gets: packed with
    pointers, slotted without — and slotted with skip ranges, which make the
    text non-contiguous, so packing cannot lay it out."""
    if skips:
        return WriteMode.SLOTTED
    return WriteMode.PACKED if pointers else WriteMode.SLOTTED


def with_region(config: BlockConfig, start: int, stop: int) -> BlockConfig:
    """``config`` read over bytes ``start`` to ``stop``: what a new block made
    from a reading and a selection is.

    The source keeps its kind — the pointers of a table in that stretch, a
    list's pointers as a table of them — and what named addresses of the old
    region (skip ranges, the bound) goes with it.
    """
    s = config.source
    source: Source
    if isinstance(s, PointerTableSource | NestedPointerSource):
        source = replace(s, start=start, stop=stop)
    elif isinstance(s, PointerListSource):
        source = PointerTableSource(
            start,
            stop,
            s.size,
            s.size,
            s.endian,
            s.mapping_id,
            s.offset,
            s.bank,
            s.null,
        )
    else:
        source = RangeSource(start, stop)
    return replace(config, source=source, skips=(), bound=None)


DEFAULT_FILL = b"\xff"


def fill_run(fill: bytes, length: int) -> bytes:
    """``length`` bytes of the ``fill`` pattern, from its first byte."""
    if length <= 0:
        return b""
    pattern = fill or DEFAULT_FILL
    return (pattern * -(-length // len(pattern)))[:length]


def is_fill(data: bytes, fill: bytes) -> bool:
    """Whether ``data`` is nothing but the ``fill`` pattern from its first byte,
    the last repeat allowed to be cut short — what :func:`fill_run` lays down."""
    return data == fill_run(fill, len(data))


def parse_fill(text: str) -> bytes:
    """A fill pattern as a configuration spells it: ``$`` and hex digits, a
    byte for every two (``$FFFF`` is two bytes), or a decimal byte."""
    text = text.strip()
    if text.startswith("$"):
        digits = text[1:]
        if not digits:
            raise ValueError("empty fill")
        digits = digits.zfill(len(digits) + len(digits) % 2)
        return bytes.fromhex(digits)
    value = int(text, 10)
    if not 0 <= value <= 0xFF:
        raise ValueError(f"fill {value} is not a byte")
    return bytes([value])


def format_fill(fill: bytes) -> str:
    """``fill`` spelled for a configuration: ``$`` and every byte in hex."""
    return "$" + fill.hex().upper()


class Status(Enum):
    UNTOUCHED = "untouched"
    EDITED = "edited"
    REVIEW = "review"
    DONE = "done"


HELD = (Status.REVIEW, Status.DONE)
"""The statuses set by hand, which the bytes do not settle: a string marked
for review or done stays so whatever its text does."""


@dataclass
class StringRecord:
    index: int
    start_bit: int
    """First bit of the string in the decompressed buffer."""
    end_bit: int
    """Exclusive bit after the last bit the string owns."""
    tokens: list[Token]
    """The decode of the bytes as they are now: the string's **current** text."""
    pointers: tuple[PointerRef, ...] = ()
    original: str = ""
    """The string's text as it was when the block was made: a snapshot the
    project keeps, not a reading of the bytes. An extraction seeds it from the
    tokens and the project's saved state replaces it."""
    replacement: str | None = None
    """Text to encode in place of the bytes on the next layout; ``None`` keeps
    the bytes as they are. Transient: set for one layout and cleared after."""
    status: Status = Status.UNTOUCHED
    """*review* when set by hand or by an import; else *edited* or *untouched*
    as the current text differs from the original or not
    (:meth:`refresh_status`)."""
    notes: str = ""
    notices: list[Notice] = field(default_factory=list)
    lines: tuple[int, ...] = ()
    """Token indices where fixed-line pieces start (fixed-line layout only)."""
    _text: tuple[tuple[int, int], str] | None = field(
        default=None, repr=False, compare=False
    )
    """The tokens rendered, with which token list they were rendered from: the
    Strings grid asks for it more than once per string, thousands at a time."""

    def __setattr__(self, name: str, value: object) -> None:
        # Text is NFC however it arrived — typed, imported from a script, a
        # translator file or a PO — so it compares and encodes against NFC
        # table text.
        if name in ("replacement", "original") and isinstance(value, str):
            value = nfc(value)
        object.__setattr__(self, name, value)

    @property
    def start(self) -> int:
        """First byte."""
        return self.start_bit // 8

    @property
    def end(self) -> int:
        """Exclusive end byte, rounded up."""
        return -(-self.end_bit // 8)

    @property
    def length(self) -> int:
        return self.end - self.start

    def pieces(self, skips=()) -> list[tuple[int, int]]:
        """The byte ranges the string occupies, in reading order.

        One range, or two for a string read across a skip range: the bytes up
        to where the skip begins, then those from where it lands to the end.
        A backwards skip lands the end before the start, so ``length`` alone
        would count that string as nothing.
        """
        start, end = self.start, self.end
        for a, b in skips:
            # Reading reached the skip when the string ends past it -- or, for
            # a skip that lands behind its start, no later than it began.
            if start <= a and (end <= start if b < a else a < end):
                return [(start, a), (b, end)]
        return [(start, end)]

    def byte_length(self, skips=()) -> int:
        """How many bytes the string occupies, whatever its pieces."""
        return sum(b - a for a, b in self.pieces(skips))

    def current_text(self) -> str:
        """The tokens rendered: what the bytes say now."""
        from mapchar.core.tokens import render

        key = (id(self.tokens), len(self.tokens))
        if self._text is None or self._text[0] != key:
            self._text = (key, render(self.tokens))
        return self._text[1]

    def original_text(self) -> str:
        """The snapshot the project keeps (:attr:`original`)."""
        return self.original

    @property
    def edited(self) -> bool:
        """Whether the bytes now say something other than the original."""
        return not self.matches_original(self.current_text())

    def matches_original(self, text: str) -> bool:
        """Whether ``text`` is the original text, line breaks and form aside."""
        return same_text(text, self.original)

    def refresh_status(self) -> None:
        """Settle *edited* or *untouched* from the texts; *review* and *done*
        stay."""
        if self.status not in HELD:
            self.status = Status.EDITED if self.edited else Status.UNTOUCHED


def string_groups(
    config: BlockConfig, strings: list[StringRecord]
) -> list[list[StringRecord]]:
    """The block's strings in the groups a layout handles apart, each in the
    order given.

    One group of every string, except for a nested source, whose records each
    reach strings of their own through their inner table: those are a group,
    told by the base the string's first pointer counts from, since what lies
    between one group's text and the next is not the block's to write over.
    """
    if not isinstance(config.source, NestedPointerSource):
        return [strings] if strings else []
    groups: dict[int | None, list[StringRecord]] = {}
    for rec in strings:
        key = rec.pointers[0].offset if rec.pointers else None
        groups.setdefault(key, []).append(rec)
    return list(groups.values())


def block_bound(config: BlockConfig, strings: list[StringRecord]) -> int:
    """The exclusive end packed strings may not cross.

    The configured bound; else a range source's stop; else the last string's
    original end (a pointer table's stop bounds pointers, not text).
    """
    if config.bound is not None:
        return config.bound
    if isinstance(config.source, RangeSource):
        return config.source.stop
    return strings[-1].end if strings else 0


@dataclass
class Extraction:
    strings: list[StringRecord]
    notices: list[Notice] = field(default_factory=list)
