"""Blocks and strings: how a region of a file is cut into editable units."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field, replace
from enum import Enum

from mapchar.core.fill import DEFAULT_FILL, fill_bits
from mapchar.core.notices import Notice
from mapchar.core.table import TableSet, TokenKind
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


MAX_RECORD_HEADER = 255
"""The largest record header, in bytes: what the Header control sets, and what
a configuration line may name."""


@dataclass(frozen=True)
class BlockConfig:
    source: Source
    string_type: StringType = EndToken()
    table_id: str = ""
    strings_per_pointer: int = 1
    """How many end-token strings the **run** a pointer reaches holds: the
    pointer lands on the first and the game counts end tokens to the rest, so
    each is a string of its own and only the first carries the pointer."""
    run_to_next: bool = False
    """A run ends where the next pointer lands, and only the last pointer's
    holds :attr:`strings_per_pointer` strings."""
    realign: tuple[int, int] = (0, 0)
    """``(multiple, offset)`` in bytes; a multiple of 0 disables."""
    skips: tuple[tuple[int, int], ...] = ()
    """``(from, to)`` byte pairs: reading ``from`` continues at ``to``."""
    header: int = 0
    """Bytes in front of every string of a range that are not text — a
    record's position, id or flags — stepped over on the way to the string and
    left standing by a write. A pointer reaches its string past any header, so
    a pointer source has no use for it."""
    line_length: int = 0
    """For fixed strings, split each into lines this long (0: off)."""
    bound: int | None = None
    """Exclusive end address strings may not cross on write."""
    write_mode: WriteMode | None = None
    """``None`` picks packed with pointers and slotted without."""
    fill: bytes = DEFAULT_FILL
    """The pattern that pads unused room, repeated from the start of the room
    it fills (:func:`~mapchar.core.fill.fill_run`)."""
    end_is_fill: bool = False
    """A fill that is the table's end token still reads as padding between
    strings (:func:`fill_reads_as_padding`)."""
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
    def reads_runs(self) -> bool:
        """Whether a pointer reaches a run of end-token strings rather than
        one: the game then counts end tokens, so every one of them is a
        string's and none is padding."""
        return (
            self.has_pointers
            and isinstance(self.string_type, EndToken)
            and (self.run_to_next or self.strings_per_pointer > 1)
        )

    @property
    def fixed_length(self) -> int | None:
        """The byte length every string has, when they all have one."""
        if isinstance(self.string_type, FixedLength):
            return self.string_type.length
        return None

    @property
    def effective_write_mode(self) -> WriteMode:
        """How this block is written. Skip ranges and a record header force
        slotted whatever :attr:`write_mode` says: packing lays the strings end
        to end, over the very bytes they step around."""
        forced = bool(self.skips) or bool(self.record_header)
        if self.write_mode is not None and not forced:
            return self.write_mode
        return default_write_mode(self.has_pointers, forced)

    @property
    def record_header(self) -> int:
        """:attr:`header` where it applies: over a range."""
        return self.header if isinstance(self.source, RangeSource) else 0


def default_write_mode(pointers: bool, skips: bool) -> WriteMode:
    """The write mode a block without one of its own gets: packed with
    pointers, slotted without — and slotted with skip ranges or record
    headers, which make the text non-contiguous, so packing cannot lay it
    out."""
    if skips:
        return WriteMode.SLOTTED
    return WriteMode.PACKED if pointers else WriteMode.SLOTTED


READING_FIELDS = (
    "source",
    "string_type",
    "strings_per_pointer",
    "run_to_next",
    "end_is_fill",
    "realign",
    "skips",
    "header",
    "line_length",
)
"""What cuts a block's strings out of the bytes. The rest of a reading — the
table the translation is written in, the bound, the fill, the labels — is what
one adjusts while editing, and leaves every string where it is."""


def recuts_strings(before: BlockConfig | None, after: BlockConfig | None) -> bool:
    """Whether the change from ``before`` to ``after`` cuts the block's strings
    out of the bytes differently (:data:`READING_FIELDS`)."""
    if before is None or after is None:
        return before is not after
    return any(getattr(before, f) != getattr(after, f) for f in READING_FIELDS)


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


def fill_reads_as_padding(config: BlockConfig, tables: TableSet) -> bool:
    """Whether a run of the block's fill between its strings is padding rather
    than text.

    A fill pattern no entry of the start table can begin a token with is
    padding wherever it sits: nothing in the block reads as it, so what a
    shorter replacement left behind is safe to pass over. A fill the table
    does map is text — a string may begin with it, or be nothing but it — and
    is read like any other bytes, which is why a block's fill should be one no
    string begins with. The reading spells the same rule in bits
    (:func:`~mapchar.pipeline.extract.padding_bits`).

    The one fill the table maps that may still be padding is its end token,
    where the block says so (:attr:`BlockConfig.end_is_fill`): slots padded
    with the byte that ends their strings. Never in a block whose pointers
    reach runs (:attr:`BlockConfig.reads_runs`), where an end token straight
    after another is an empty string the game counts.
    """
    pad = fill_bits(config.fill)
    if not pad:
        return False
    if config.end_is_fill and not config.reads_runs and _is_end_token(pad, tables):
        return True
    return not any(
        key.startswith(pad) or pad.startswith(key) for key in tables.start.entries
    )


def _is_end_token(pad: str, tables: TableSet) -> bool:
    """Whether the bits ``pad`` are one of the start table's end tokens."""
    entry = tables.start.entries.get(pad)
    return entry is not None and entry.kind is TokenKind.END


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
    digest: int | None = None
    """A checksum of the bits the string holds now (:func:`bits_digest`)."""
    original_digest: int | None = None
    """:attr:`digest` as it was when :attr:`original` was taken, which the
    project keeps beside it. ``None`` for an original saved before digests
    were: :attr:`edited` then goes by the text."""
    replacement: str | None = None
    """Text to encode in place of the bytes on the next layout; ``None`` keeps
    the bytes as they are. Transient: set for one layout and cleared after."""
    status: Status = Status.UNTOUCHED
    """*review* when set by hand or by an import; else *edited* or *untouched*
    as the bytes differ from the original's or not (:meth:`refresh_status`)."""
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
        """Whether the string is no longer the original.

        Told by the bytes first: a block switched to the table its translation
        is written in reads every string as other text, and none of them has
        been touched. Untouched either way, though — bytes that say the
        original text are the original however they spell it, since a re-encode
        may reach the same text through other codes. An original kept without a
        digest goes by the text alone.
        """
        if self.original_digest is None or self.digest is None:
            return not self.matches_original(self.current_text())
        return self.digest != self.original_digest and not self.matches_original(
            self.current_text()
        )

    def matches_original(self, text: str) -> bool:
        """Whether ``text`` is the original text, line breaks and form aside."""
        return same_text(text, self.original)

    def refresh_status(self) -> None:
        """Settle *edited* or *untouched* from the bytes; *review* and *done*
        stay. An original kept without a digest takes that of bytes that still
        say it, so it goes by the bytes from then on."""
        if self.original_digest is None and not self.edited:
            self.original_digest = self.digest
        if self.status not in HELD:
            self.status = Status.EDITED if self.edited else Status.UNTOUCHED


def bits_digest(bits: str) -> int:
    """A checksum of a string's bits, spelled as ``Bits.window`` spells them:
    what tells an untouched string from an edited one without keeping a byte
    of the ROM."""
    return zlib.crc32(bits.encode("ascii"))


def grouped_strings(
    config: BlockConfig, strings: list[StringRecord]
) -> list[tuple[int | None, list[StringRecord]]]:
    """The block's strings in the groups a layout handles apart, each with the
    base its pointers count from and in the order given.

    One group of every string, under no base, except for a nested source, whose
    records each reach strings of their own through their inner table: those
    are a group, told by the base the string's first pointer counts from, since
    what lies between one group's text and the next is not the block's to write
    over. Where pointers reach runs (:attr:`BlockConfig.reads_runs`), a string
    with no pointer is the rest of one, in the group of the string before it.
    """
    if not isinstance(config.source, NestedPointerSource):
        return [(None, strings)] if strings else []
    groups: dict[int | None, list[StringRecord]] = {}
    key = None
    for rec in strings:
        if rec.pointers or not config.reads_runs:
            key = rec.pointers[0].offset if rec.pointers else None
        groups.setdefault(key, []).append(rec)
    return list(groups.items())


def string_groups(
    config: BlockConfig, strings: list[StringRecord]
) -> list[list[StringRecord]]:
    """The block's strings in the groups a layout handles apart
    (:func:`grouped_strings`), without the base each is told by."""
    return [group for _, group in grouped_strings(config, strings)]


def block_bound(
    config: BlockConfig,
    strings: list[StringRecord],
    room: int | None = None,
) -> int:
    """The exclusive end packed strings may not cross.

    The configured bound; else a range source's stop; else the end of the text
    the pointers reach (a pointer table's stop bounds pointers, not text), and
    never earlier than the ``room`` the block remembers
    (:func:`remembered_room`) — so room a shortened string gave up is room to
    take back. Nothing past the text is claimed on the strength of the
    bytes standing there: what lies beyond is the next block's, whatever it
    holds.
    """
    if config.bound is not None:
        return config.bound
    if isinstance(config.source, RangeSource):
        return config.source.stop
    end = max((rec.end for rec in strings), default=0)
    return max(end, room or 0)


def remembered_room(
    config: BlockConfig, bound: int, strings: list[StringRecord]
) -> int | None:
    """The room a write leaves the block remembering, or ``None`` for none.

    ``bound`` is the bound the block had going into the write and ``strings``
    are its strings after it: text that now ends earlier leaves the bound it
    had as the block's own extent, which :func:`block_bound` hands back to the
    next edit. Only a block whose bound is that default has room to remember —
    a configured bound and a range source say where the room ends themselves,
    and a nested source's groups are bounded one by one
    (:func:`~mapchar.pipeline.insert.group_bounds`).
    """
    if (
        config.bound is not None
        or not config.has_pointers
        or isinstance(config.source, NestedPointerSource)
    ):
        return None
    end = max((rec.end for rec in strings), default=0)
    return bound if end < bound else None


@dataclass
class Extraction:
    strings: list[StringRecord]
    notices: list[Notice] = field(default_factory=list)
    inner_tables: dict[int, int] = field(default_factory=dict)
    """A nested source's inner pointer table address, by the base its pointers
    count from — which is what :func:`string_groups` keys a group by, so a
    group can say which table reached it. Empty for every other source."""
