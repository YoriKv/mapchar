"""PRS — Sega's LZ + RLE bit-stream codec.

The general-purpose compression Sega shipped across the Saturn and Dreamcast
eras, used for script, map data and packed archives alike. A stream is a
sequence of ops selected by control bits; the bits live in **control bytes
interleaved into the byte stream**, each supplying eight selectors LSB first and
each fetched only at the moment its first bit is needed. That laziness is part of
the format, not an implementation detail: a control byte sits between the operand
bytes it describes and the ones before it, so a writer that emits control bytes
eagerly produces a stream this decoder will not read back.

Ops, in the order their selector bits are read::

    1                literal - the next byte, verbatim
    0 0 <2 bits>     short copy - length 2..5 (the 2 bits + 2), then one byte
                     giving distance 1..256 (as ``byte - 256``)
    0 1 <2 bytes>    long copy - little-endian word:
                       distance = 8192 - (word >> 3)      (1..8192)
                       n        = word & 7
                       n != 0 -> length = n + 2            (3..9)
                       n == 0 -> length = next byte + 1    (1..256)
                     word == 0 is the **end-of-stream marker**

Copies read from the output produced so far and may overlap it, so a distance
shorter than the length is a run — that is the format's RLE.

Because ``word == 0`` terminates, a long copy must never encode distance 8192
together with the extended-length form; the compressor caps distance at 8191 and
sidesteps the collision entirely.

The compressor is a greedy parse by benefit — bits saved against literals — with
a one-step lazy deferral, over a 3-byte-prefix index. It does not emit
two-byte matches: they are only reachable through the short copy, save 6 bits
against two literals, and indexing 2-byte prefixes to find them costs far more
than that in search time. Byte-identity with a particular original blob is a
non-goal; round-tripping is the contract.
"""

from __future__ import annotations

from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_OUT, stream_error
from mapchar.plugins.builtins.compression._lz import MatchFinder, copy_back

SHORT_MAX_DISTANCE = 256
SHORT_MAX_LENGTH = 5
# One below the 8192 the 13-bit field reaches: distance 8192 with the extended
# length form is the end-of-stream word, so the compressor never goes there.
LONG_MAX_DISTANCE = 8191
LONG_MAX_LENGTH = 256

MIN_MATCH = 3

# Op costs in bits, literals included, for the greedy parse's benefit test.
_BITS_LITERAL = 1 + 8
_BITS_SHORT = 2 + 2 + 8
_BITS_LONG = 2 + 16
_BITS_LONG_EXTENDED = 2 + 16 + 8

_SCHEME = "PRS"


class _Truncated(ValueError):
    """The buffer ended mid-op — recoverable under a partial decode, unlike a
    stream whose own structure is wrong."""

    def __init__(self, reason: str) -> None:
        super().__init__(str(stream_error(_SCHEME, reason)))


class _BitReader:
    """The interleaved control-bit reader: one control byte per eight selectors."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.control = 0
        self.mask = 0  # 0 means "no bits left" - the next read fetches a byte

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise _Truncated("source ended mid-op")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def bit(self) -> int:
        if self.mask == 0:
            self.control = self.byte()
            self.mask = 1
        value = 1 if self.control & self.mask else 0
        self.mask = (self.mask << 1) & 0xFF
        return value


class _BitWriter:
    """The writer half: control bytes allocated lazily, exactly as read back."""

    def __init__(self) -> None:
        self.out = bytearray()
        self.control_at = -1
        self.bit_index = 8  # forces the first bit to allocate a control byte

    def bit(self, value: int) -> None:
        if self.bit_index >= 8:
            self.control_at = len(self.out)
            self.out.append(0)
            self.bit_index = 0
        if value:
            self.out[self.control_at] |= 1 << self.bit_index
        self.bit_index += 1

    def byte(self, value: int) -> None:
        self.out.append(value & 0xFF)


def _copy(out: bytearray, distance: int, length: int, what: str) -> None:
    """:func:`copy_back` with the reach-before-the-output check PRS words itself.

    ``what`` names the op that asked — short or long — which the message needs and
    the copy does not: the two are the same copy once decoded, so the reader that
    just read one is the only place that can still say which overreached.
    """
    if distance > len(out):
        raise stream_error(
            _SCHEME, f"{what} copy reaches before the start of the output"
        )
    copy_back(out, distance, length)


def decompress(data: bytes, *, partial: bool = False) -> tuple[bytes, int, bool]:
    """Decode a PRS stream.

    Returns ``(output, consumed, complete)``. ``complete`` is true when the
    end-of-stream marker was reached inside the buffer, making ``consumed`` the
    structure's true byte length. With ``partial`` a buffer that ends mid-stream
    yields everything decoded up to the last whole op instead of raising — what a
    bounded view window needs.
    """
    reader = _BitReader(data)
    out = bytearray()
    # Rewind point: the end of the last op that completed, so a truncated buffer
    # reports whole ops rather than half a copy.
    safe_len, safe_pos = 0, 0

    try:
        while True:
            if len(out) >= MAX_OUT:
                # Past any structure a game unpacks in one go: the bytes were not
                # a stream, whatever they decoded to.
                raise _Truncated("output past the cap")
            if reader.bit():
                out.append(reader.byte())
            elif reader.bit():  # long copy
                word = reader.byte() | (reader.byte() << 8)
                if word == 0:
                    return bytes(out), reader.pos, True
                distance = 8192 - (word >> 3)
                n = word & 7
                length = reader.byte() + 1 if n == 0 else n + 2
                _copy(out, distance, length, "long")
            else:  # short copy
                length = ((reader.bit() << 1) | reader.bit()) + 2
                _copy(out, SHORT_MAX_DISTANCE - reader.byte(), length, "short")
            safe_len, safe_pos = len(out), reader.pos
    except _Truncated:
        if not partial:
            raise
    return bytes(out[:safe_len]), safe_pos, False


# -- compression ------------------------------------------------------------


def _op_bits(length: int, distance: int) -> int:
    """What encoding this match costs, in bits — the cheapest op that fits it."""
    if distance <= SHORT_MAX_DISTANCE and length <= SHORT_MAX_LENGTH:
        return _BITS_SHORT
    return _BITS_LONG if length <= 9 else _BITS_LONG_EXTENDED


def compress(data: bytes) -> bytes:
    """Encode raw bytes into a PRS stream."""
    n = len(data)
    writer = _BitWriter()
    # Scored rather than longest-wins, so this walks the chain itself: PRS has two
    # back-reference ops of different cost, and a nearer short match written as the
    # cheap one can beat a distant long one.
    finder = MatchFinder(data, min_match=MIN_MATCH, window=LONG_MAX_DISTANCE)

    def best_match(pos: int) -> tuple[int, int, int]:
        """The most profitable match at ``pos``, as ``(benefit, length, distance)``.

        Benefit is bits saved against encoding the same bytes as literals, so a
        nearer-but-shorter match can win over a distant longer one whenever the
        cheaper op offsets the bytes given up.
        """
        limit = min(LONG_MAX_LENGTH, n - pos)
        if limit < MIN_MATCH:
            return 0, 0, 0
        best = (0, 0, 0)
        for candidate in finder.candidates(pos):
            # The shortest match that could possibly beat the best benefit so
            # far. No op costs less than _BITS_SHORT, so a match of L bytes is
            # worth at most L * _BITS_LITERAL - _BITS_SHORT however near it is —
            # and a candidate that cannot reach that length need not be measured.
            # Candidates arrive nearest-first and benefit falls with distance, so
            # this only tightens as the walk goes on.
            need = (best[0] + _BITS_SHORT) // _BITS_LITERAL + 1
            if need > limit:
                break  # nothing left in the chain can pay for itself
            if not finder.can_reach(pos, candidate, max(need, MIN_MATCH)):
                continue
            distance = pos - candidate
            length = finder.match_length(pos, candidate, limit)
            if length < MIN_MATCH:
                continue
            benefit = length * _BITS_LITERAL - _op_bits(length, distance)
            if benefit > best[0]:
                best = (benefit, length, distance)
        return best

    pos = 0
    while pos < n:
        benefit, length, distance = best_match(pos)
        finder.add(pos)  # index it before any lookahead reads it
        if benefit > 0 and pos + 1 < n:
            # One-step lazy deferral: a strictly better match one byte along is
            # worth more than this one plus the literal it displaces.
            next_benefit, _, _ = best_match(pos + 1)
            if next_benefit > benefit + _BITS_LITERAL:
                benefit = 0

        if benefit <= 0:
            writer.bit(1)
            writer.byte(data[pos])
            pos += 1
            continue

        if distance <= SHORT_MAX_DISTANCE and length <= SHORT_MAX_LENGTH:
            writer.bit(0)
            writer.bit(0)
            writer.bit((length - 2) >> 1)
            writer.bit((length - 2) & 1)
            writer.byte(SHORT_MAX_DISTANCE - distance)
        else:
            writer.bit(0)
            writer.bit(1)
            word = ((8192 - distance) << 3) & 0xFFF8
            if length <= 9:
                word |= length - 2
                writer.byte(word & 0xFF)
                writer.byte(word >> 8)
            else:
                writer.byte(word & 0xFF)
                writer.byte(word >> 8)
                writer.byte(length - 1)
        finder.add_run(pos + 1, pos + length)
        pos += length

    writer.bit(0)  # end of stream: a long copy whose word is zero
    writer.bit(1)
    writer.byte(0)
    writer.byte(0)
    return bytes(writer.out)


class Prs(PartialDecompression):
    info = PluginInfo("prs", "PRS (Sega LZ + RLE)", Stage.COMPRESSION, "Sega")

    _decode = staticmethod(decompress)
    _encode = staticmethod(compress)
