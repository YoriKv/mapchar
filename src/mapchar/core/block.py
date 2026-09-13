"""Blocks and strings: how a region of a file is cut into editable units."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mapchar.core.notices import Notice
from mapchar.core.text import nfc
from mapchar.core.tokens import Token


@dataclass(frozen=True)
class RangeSource:
    """Consecutive strings from ``start`` to ``stop`` (exclusive), in bytes."""

    start: int
    stop: int


@dataclass(frozen=True)
class FixedSource:
    """``count`` strings of exactly ``length`` bytes from ``start``."""

    start: int
    count: int
    length: int


@dataclass(frozen=True)
class PointerRef:
    address: int
    size: int
    endian: str
    mapping_id: str
    offset: int
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


@dataclass(frozen=True)
class PointerListSource:
    addresses: tuple[int, ...]
    size: int
    endian: str = "little"
    mapping_id: str = "linear"
    offset: int = 0
    bank: int = 0


Source = RangeSource | FixedSource | PointerTableSource | PointerListSource


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


StringType = EndToken | FixedLength | Pascal | NextPointer


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
    fill: int = 0xFF
    show_end: bool = False
    """Append an artificial ``[end]`` code to every fixed string."""
    end_label: str = "end"
    """Label of the artificial end code shown after fixed strings."""
    line_label: str = "line"
    """Label of the artificial line code shown between fixed lines."""

    @property
    def has_pointers(self) -> bool:
        return isinstance(self.source, PointerTableSource | PointerListSource)

    @property
    def fixed_length(self) -> int | None:
        """The byte length every string has, when they all have one."""
        if isinstance(self.string_type, FixedLength):
            return self.string_type.length
        if isinstance(self.source, FixedSource):
            return self.source.length
        return None

    @property
    def effective_write_mode(self) -> WriteMode:
        if self.write_mode is not None:
            return self.write_mode
        if isinstance(self.source, FixedSource) or self.skips:
            # Skip ranges make the text non-contiguous; packing cannot lay it out.
            return WriteMode.SLOTTED
        return WriteMode.PACKED if self.has_pointers else WriteMode.SLOTTED


class Status(Enum):
    UNTOUCHED = "untouched"
    EDITED = "edited"
    REVIEW = "review"


@dataclass
class StringRecord:
    index: int
    start_bit: int
    """First bit of the string in the decompressed buffer."""
    end_bit: int
    """Exclusive bit after the last bit the string owns."""
    original: list[Token]
    pointers: tuple[PointerRef, ...] = ()
    translation: str | None = None
    status: Status = Status.UNTOUCHED
    notes: str = ""
    notices: list[Notice] = field(default_factory=list)
    lines: tuple[int, ...] = ()
    """Token indices where fixed-line pieces start (fixed-line layout only)."""

    def __setattr__(self, name: str, value: object) -> None:
        # A translation is NFC however it arrived — typed, imported from a
        # script, a translator file or a PO — so it compares and encodes
        # against NFC table text.
        if name == "translation" and isinstance(value, str):
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

    def original_text(self) -> str:
        from mapchar.core.tokens import render

        return render(self.original)

    def current_text(self) -> str:
        """The translation when there is one, else the original text."""
        return (
            self.translation if self.translation is not None else self.original_text()
        )

    def matches_original(self, text: str) -> bool:
        """Whether ``text`` is the original text, line breaks and form aside."""
        return nfc(text).replace("\n", "") == nfc(self.original_text()).replace(
            "\n", ""
        )


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
