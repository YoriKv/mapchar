"""Kosinski — the Mega Drive's general-purpose LZSS, both directions.

The general-purpose half of Sega's shipped toolchain: it makes no assumption
about its payload at all, and games put script, level layouts, block mappings,
code and art behind it alike.

It is an ordinary sliding-window LZSS with a **16-bit descriptor word**, and the
three things that make it its own format are all in how that word is handled::

    descriptor  u16 little-endian, bits consumed LOW bit first

    1            one literal byte follows
    0 0 h l      inline match: length ((h<<1)|l) + 2, one byte follows,
                 distance 0x100 - byte                        (2..5 bytes back
                                                               within 256)
    0 1          separate match: two bytes low, high follow
                   count = high & 7
                   count != 0   length = count + 2            (3..9)
                   count == 0   a third byte follows:
                                  0  end of stream
                                  1  no-op (a module boundary)
                                  n  length = n + 1           (3..256)
                 distance = 0x2000 - (((high & 0xF8) << 5) | low)

Four things about that are easy to get wrong, and three of them are silent:

- **The descriptor word is little-endian and its bits come out LSB first.** Read
  it big-endian, or MSB first, and the first few ops still decode — the wrong
  ones — so the failure looks like corrupt data rather than a byte-order slip.
- **The next descriptor is fetched the moment the last bit of the current one is
  spent**, not when the next bit is wanted. So the new word sits *before* the
  payload bytes of the op whose final descriptor bit just came out. A lazy
  refill reads exactly the same bits in exactly the same order and still lands on
  the wrong bytes.
- **That eager fetch is why a stream can end with a descriptor word nobody
  reads.** When the terminator's own two bits happen to fill a word, an encoder
  has to emit a dummy word before the terminator's bytes, or the decoder eats
  them as a descriptor. :meth:`_Writer.finish` does.
- **A match may reach past the byte being written**, into output that does not
  exist yet, because the decoder copies one byte at a time — so the source
  repeats with period ``distance``. That is not a corner case, it is how the
  format run-length-encodes a fill.

**Stream length is byte-exact, not word-rounded**: the 68000 decompressor reads
it a byte at a time, so the stream ends where its last byte does. Some encoders
pad the result to an even length with a trailing zero; that byte is nobody's to
read, and a stream that carries one reports one fewer byte consumed than it
occupies.
"""

from __future__ import annotations

from collections.abc import Iterator

from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_OUT, stream_error
from mapchar.plugins.builtins.compression._lz import (
    InterleavedWriter,
    MatchFinder,
    Truncated,
    copy_back_checked,
    parse_shortest,
)

DESC_BYTES = 2
DESC_BITS = DESC_BYTES * 8

INLINE_WINDOW = 0x100  # what one distance byte reaches
FULL_WINDOW = 0x2000  # what the 13-bit distance field reaches

INLINE_MIN, INLINE_MAX = 2, 5
SHORT_MIN, SHORT_MAX = 3, 9
LONG_MIN, LONG_MAX = 10, 256

COUNT_MASK = 0x07
DISTANCE_HIGH = 0xF8

END_MARKER = (0x00, 0xF0, 0x00)

_SCHEME = "Kosinski"

# What each form costs to write, descriptor bits included. The parse is a
# shortest path over these, so they have to be the *whole* cost: a descriptor
# bit is as real as a payload byte, and an op that saves a byte while spending
# four extra descriptor bits is not the bargain it looks.
LITERAL_COST = 1 + 8
INLINE_COST = 4 + 8
SHORT_COST = 2 + 16
LONG_COST = 2 + 24
END_COST = 2 + 24

OP_LITERAL, OP_INLINE, OP_SHORT, OP_LONG = range(4)


class _Reader:
    """Descriptor bits and payload bytes over one buffer, sharing a position.

    They have to share it: the two are interleaved in the stream, and the whole
    point of the eager refill described in the module docstring is *where* a
    descriptor word lands relative to the bytes around it.
    """

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self._data = data
        self.pos = pos
        self._word = 0
        self._left = 0
        self._fill()

    def _fill(self) -> None:
        chunk = self._data[self.pos : self.pos + DESC_BYTES]
        self.pos += len(chunk)
        # Short reads pad with zeros rather than raising: a bounded window can end
        # on the dummy word, and the op that then decodes out of the padding is
        # what raises, with the position to say where.
        self._word = int.from_bytes(chunk.ljust(DESC_BYTES, b"\0"), "little")
        self._left = DESC_BITS

    def bit(self) -> int:
        value = self._word & 1
        self._word >>= 1
        self._left -= 1
        if not self._left:
            self._fill()
        return value

    def byte(self) -> int:
        if self.pos >= len(self._data):
            raise Truncated
        value = self._data[self.pos]
        self.pos += 1
        return value


def decompress(data: bytes, *, partial: bool = False) -> tuple[bytes, int, bool]:
    """Unpack an LZSS stream.

    Returns ``(plain, consumed, complete)``; ``complete`` is false only when the
    buffer ran out before the end marker, which ``partial`` downgrades from an
    error to a short result.
    """
    if len(data) < DESC_BYTES:
        raise stream_error(
            _SCHEME, f"shorter than the {DESC_BYTES}-byte descriptor word"
        )
    reader = _Reader(data)
    out = bytearray()
    complete = False
    try:
        while True:
            if len(out) >= MAX_OUT:
                # Past any structure a cartridge unpacks in one go: the bytes were
                # not a stream, whatever they decoded to.
                raise Truncated
            if reader.bit():
                out.append(reader.byte())
                continue
            if reader.bit():
                low = reader.byte()
                high = reader.byte()
                count = high & COUNT_MASK
                if count:
                    length = count + 2
                else:
                    count = reader.byte()
                    if count == 0:
                        complete = True
                        break
                    if count == 1:
                        # A module boundary in the chunked variant: no output,
                        # and passing it by is what lets a plain decoder read one.
                        continue
                    length = count + 1
                distance = FULL_WINDOW - (((high & DISTANCE_HIGH) << 5) | low)
            else:
                length = ((reader.bit() << 1) | reader.bit()) + INLINE_MIN
                distance = INLINE_WINDOW - reader.byte()
            copy_back_checked(out, distance, length, _SCHEME)
    except Truncated:
        if not partial:
            raise stream_error(
                _SCHEME, f"source ended after {len(out):,} bytes"
            ) from None

    if not complete and not partial:
        raise stream_error(_SCHEME, f"source ended after {len(out):,} bytes")
    return bytes(out), reader.pos, complete


# -- compression ------------------------------------------------------------


class _Writer(InterleavedWriter):
    """The shared writer under Kosinski's descriptor word and its dummy.

    ``eager`` because the decoder's refill is (see the module docstring): it
    fetches the next word the moment the last bit of the current one is spent, so
    a payload byte written with no word open still queues behind the word that is
    about to open.
    """

    def __init__(self) -> None:
        super().__init__(word_bits=DESC_BITS, low_first=True, eager=True)

    def bit(self, value: int) -> None:
        self.put(value, 1)

    def byte(self, value: int) -> None:
        self._pending.append(value)

    def finish(self) -> bytes:
        if self._bits:
            self.out += self._word.to_bytes(DESC_BYTES, "little")
        else:
            # See the module docstring: the terminator's bits exactly filled a
            # word, so the decoder has already fetched whatever comes next. Give
            # it an empty word rather than the terminator's own bytes.
            self.out += bytes(DESC_BYTES)
        self.out += self._pending
        return bytes(self.out)


def compress(data: bytes) -> bytes:
    """Encode ``data`` as one Kosinski stream, as small as the forms allow.

    The parse is a shortest path, not a greedy walk: ``cost[i]`` is the fewest
    *bits* — descriptor included — that can encode ``data[i:]``, solved
    right-to-left, so every form is priced against what the rest of the stream
    then costs. Greedy has to guess, and here the guess is unusually easy to get
    wrong: the longest match is frequently the expensive form, and two inline
    matches at twelve bits each beat one long match at twenty-six.

    Two searches, not one, because the forms disagree about how far back they can
    reach. The nearest match inside 256 bytes is what the cheap inline form can
    use, and it is routinely a different match from the longest inside 8 KiB —
    so pricing the inline form against the far match would hide it entirely.
    """
    n = len(data)
    if n == 0:
        writer = _Writer()
        writer.bit(0)
        writer.bit(1)
        for value in END_MARKER:
            writer.byte(value)
        return writer.finish()

    near_len, near_at = MatchFinder(
        data, min_match=INLINE_MIN, window=INLINE_WINDOW
    ).all_longest(INLINE_MAX)
    far_len, far_at = MatchFinder(
        data, min_match=SHORT_MIN, window=FULL_WINDOW
    ).all_longest(LONG_MAX)

    def options(
        i: int, cost: list[float]
    ) -> Iterator[tuple[int, float, tuple[int, int]]]:
        """Every form Kosinski could write at ``i``, priced whole."""
        yield 1, cost[i + 1] + LITERAL_COST, (OP_LITERAL, 0)
        # Every length each short form allows is priced, not just its longest: the
        # ranges are tiny, and `cost` is not quite monotonic, so the longest match
        # available is not always the one that leaves the cheapest tail.
        if near_len[i]:
            distance = i - near_at[i]
            for length in range(INLINE_MIN, min(near_len[i], INLINE_MAX) + 1):
                yield length, cost[i + length] + INLINE_COST, (OP_INLINE, distance)
        if far_len[i]:
            distance = i - far_at[i]
            for length in range(SHORT_MIN, min(far_len[i], SHORT_MAX) + 1):
                yield length, cost[i + length] + SHORT_COST, (OP_SHORT, distance)
            # The long form spans 10..256, too many lengths to price one by one
            # for what it recovers — a shorter long match always leaves a tail a
            # cheaper form could have covered instead, and those are priced above.
            if far_len[i] >= LONG_MIN:
                length = min(far_len[i], LONG_MAX)
                yield length, cost[i + length] + LONG_COST, (OP_LONG, distance)

    writer = _Writer()
    at = 0  # where in the input the op being written starts
    for length, (op, distance) in parse_shortest(
        n, tail_cost=END_COST, options=options
    ):
        if op == OP_LITERAL:
            writer.bit(1)
            writer.byte(data[at])
        elif op == OP_INLINE:
            code = length - INLINE_MIN
            writer.bit(0)
            writer.bit(0)
            writer.bit((code >> 1) & 1)
            writer.bit(code & 1)
            writer.byte((INLINE_WINDOW - distance) & 0xFF)
        else:
            value = FULL_WINDOW - distance
            writer.bit(0)
            writer.bit(1)
            writer.byte(value & 0xFF)
            if op == OP_SHORT:
                writer.byte(((value >> 5) & DISTANCE_HIGH) | (length - 2))
            else:
                writer.byte((value >> 5) & DISTANCE_HIGH)
                writer.byte(length - 1)
        at += length

    writer.bit(0)
    writer.bit(1)
    for value in END_MARKER:
        writer.byte(value)
    return writer.finish()


class Kosinski(PartialDecompression):
    # The end marker bounds the stream, so a ROM can chain these back to back.
    info = PluginInfo(
        "kosinski", "Kosinski (Mega Drive LZSS)", Stage.COMPRESSION, "Sega"
    )

    _decode = staticmethod(decompress)
    _encode = staticmethod(compress)
