"""Rob Northen Compression — RNC methods 1 and 2, both directions.

The packer Rob Northen Computing sold to developers on every early-90s platform,
and the one Western studios reached for on the Mega Drive: Sonic 3D Blast,
Earthworm Jim, Mickey Mania, Toy Story, Aladdin and some eighty more carry RNC
streams. A stream announces itself, which almost nothing else in this folder
does::

    header, 18 bytes, all big-endian
      +0   "RNC"             magic
      +3   u8                method: 1 or 2
      +4   u32               unpacked size
      +8   u32               packed size  (bytes after the header)
      +12  u16               CRC-16 of the unpacked data
      +14  u16               CRC-16 of the packed data
      +16  u8                leeway: how far an in-place unpack overruns
      +17  u8                chunk count
    body, starting with two flag bits: "locked" (ignored on unpack), "keyed"

The CRC is the reflected 0xA001 polynomial with a zero seed. **Both CRCs are
checked**: the packed one before decoding, so a scan that lands on stray ``RNC``
bytes is rejected without decoding anything, and the unpacked one after. Magic,
method, sizes and two CRCs together are what make a scan hit on this format
trustworthy.

**Method 1** is LZ77 with Huffman-coded fields, in chunks::

    bits   16-bit little-endian words, consumed LOW bit first
    chunk  3 tables — literal-run lengths, distances, match lengths:
             u5 symbol count (<= 16), then a u4 code length per symbol
           u16 subchunk count
           per subchunk: run    <- literal-run table, then that many raw bytes
                         unless it is the chunk's last subchunk:
                           distance <- distance table, + 1
                           length   <- length table,   + 2
    value  symbol 0 and 1 are the values 0 and 1; symbol s >= 2 is followed by
           s - 1 extra bits, value = (1 << (s - 1)) | extra

**Method 2** is a fixed-code LZ77, byte-oriented and faster to unpack::

    bits   one byte at a time, consumed HIGH bit first
    0                 literal byte
    10 ab             match, length 4 + a when b = 0 ...
    10 a 1 c          ... else length 6 + 2a + c; except length 9 means
                      a literal run: u4 n, then (n * 4) + 12 raw bytes
    110               match of length 2, distance = byte + 1
    1110              match of length 3
    1111 byte         byte != 0: match of length byte + 8
                      byte == 0: end of chunk, then one bit: 1 = more follow
    distance, for the length 3+ matches: a prefix code for the high nibble
                      (0, 110, 100x, 1x1y1, 1x1y0z), then the low byte; + 1

Four things about that are easy to get wrong:

- **Raw bytes are not in the bit stream.** Both methods read literal bytes
  straight from the byte position, *between* bit words: the word being consumed
  was fetched before them, the next word is fetched after. An encoder therefore
  holds each literal back until the word its preceding bits sit in is written.
  Method 1 adds a further twist — its decoder keeps the next 16 bits of the
  stream visible for code matching, and re-reads them after every literal run.
- **Method 1's Huffman symbols are bit-length classes, not values.** A decoder
  that returns the symbol itself reads short runs correctly and everything above
  1 wrong, in lengths that still look plausible.
- **Method 1 subchunks end on a literal run.** A count of *n* is *n* runs and
  *n - 1* matches; reading *n* matches consumes the next chunk's tables.
- **The chunk end in method 2 is a match of length 8 spelled the long way** —
  ``1111`` then a zero byte — which is why the long form starts at 9.

**Window and limits.** Method 1 reaches 32 KiB back with matches up to 4096
bytes; method 2 reaches 4096 back with matches up to 255. Both are what the
original packer used, and the encoder here stays inside them.

Encrypted ("keyed") streams are refused: the key is not in the data. The
unpacked size is capped at
:data:`~mapchar.plugins.builtins.compression._limits.MAX_OUT` like every scheme
here, since the header's 32-bit field is the one thing a corrupt stream can make
arbitrarily large.

**Byte-identity with the original packer is not the contract**, as for every
codec here: a re-encode has to unpack to the same bytes and verify under both
CRCs. Method 1 is a greedy parse with a one-step lazy deferral and per-chunk
Huffman tables; method 2 is a shortest-path parse over its fixed bit costs.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator

from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_OUT, stream_error
from mapchar.plugins.builtins.compression._lz import (
    InterleavedWriter,
    MatchFinder,
    Truncated,
    check_reach,
    copy_back,
    parse_greedy,
    parse_shortest,
)

MAGIC = b"RNC"
HEADER_SIZE = 18
METHOD_1 = 1
METHOD_2 = 2

_SCHEME = "RNC"

# How far past its own packed size a decoder may read. Method 1 fetches its bit
# words two bytes at a time, and the packer writes only one byte of a final word
# holding eight bits or fewer — so a valid stream is read a byte beyond its end.
# Anything further is a corrupt stream decoding padding, stopped here rather than
# left to run until the size check.
READ_SLACK = 4

# -- method 1 ----------------------------------------------------------------

M1_WINDOW = 0x8000  # distance - 1 must fit symbol 15: at most 15 bits
M1_MIN_MATCH = 2
M1_MAX_MATCH = 0x1000
M1_MAX_RUN = 0x7FFF  # symbol 15's largest value
M1_TABLE_SYMBOLS = 16
M1_MAX_CODE_BITS = 15  # a 4-bit length field; 16 symbols never need more
M1_CHUNK_INPUT = 0x3000  # input bytes the packer covers per chunk
# Candidates tested per position. Higher than the LZ codecs' default because the
# window is eight times theirs: at 96 the parse runs 3% larger than the original
# packer's on tile data, at 512 within 1.6%, for about twice the time.
M1_CANDIDATES = 512
M1_MAX_SUBCHUNKS = 0xFFFF
MAX_CHUNKS = 0xFF

# -- method 2 ----------------------------------------------------------------

M2_WINDOW = 0x1000
M2_NEAR_WINDOW = 0x100  # what the length-2 form's single distance byte reaches
M2_MAX_MATCH = 0xFF
M2_LONG_MIN = 9
M2_RUN_MIN, M2_RUN_MAX, M2_RUN_STEP = 12, 72, 4
M2_CHUNK_INPUT = 0x3000

# (bits, count) spelling match lengths 3..8, as indexed by length - 2; length 2
# has a form of its own and length 9 up takes the long form.
M2_LENGTH_CODES = {
    3: (0x0E, 4),
    4: (0x08, 4),
    5: (0x0A, 4),
    6: (0x12, 5),
    7: (0x13, 5),
    8: (0x16, 5),
}
M2_LENGTH2_CODE = (0x06, 3)
M2_LONG_CODE = (0x0F, 4)
M2_RUN_CODE = (0x17, 5)  # the length-9 slot of the short form
# (bits, count) spelling the high nibble of distance - 1.
M2_DISTANCE_CODES = (
    (0x00, 1), (0x06, 3), (0x08, 4), (0x09, 4),
    (0x15, 5), (0x17, 5), (0x1D, 5), (0x1F, 5),
    (0x28, 6), (0x29, 6), (0x2C, 6), (0x2D, 6),
    (0x38, 6), (0x39, 6), (0x3C, 6), (0x3D, 6),
)  # fmt: skip


def _fail(reason: str) -> ValueError:
    return stream_error(_SCHEME, reason)


def _crc_table() -> list[int]:
    table = []
    for value in range(256):
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
        table.append(value)
    return table


_CRC_TABLE = _crc_table()


def crc16(data: bytes) -> int:
    """The header's CRC-16: reflected polynomial 0xA001, zero seed."""
    crc = 0
    table = _CRC_TABLE
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc


class _Source:
    """The byte position both bit readers and the literal copies share.

    Three regions, and the distinction is the point: inside the buffer a byte is
    data; past the header's packed size it is look-ahead a valid stream never
    acts on, read as zero; and between the end of a short buffer and that packed
    size it is missing, which only ``partial`` forgives.
    """

    def __init__(self, data: bytes, end: int) -> None:
        self._data = data
        self._end = end
        self._avail = min(len(data), end)
        self.pos = HEADER_SIZE

    def peek(self, offset: int) -> int:
        at = self.pos + offset
        return self._data[at] if at < self._avail else 0

    def word_byte(self) -> int:
        """A byte of a bit word, which may legitimately lie just past the end."""
        at = self.pos
        self.pos += 1
        if at < self._avail:
            return self._data[at]
        if at < self._end:
            raise Truncated
        if at >= self._end + READ_SLACK:
            raise _fail("bit stream runs past the header's packed size")
        return 0

    def take(self, count: int, out: bytearray | None = None) -> bytes:
        """``count`` payload bytes, which must lie inside the stream.

        Given ``out``, a buffer that ends inside the bytes still hands over the
        ones it has before giving up, so a partial decode of incompressible data
        -- one long literal run -- shows something rather than nothing.
        """
        at = self.pos
        if at + count > self._end:
            raise _fail("raw bytes run past the header's packed size")
        if at + count > self._avail:
            if out is not None:
                out += self._data[at : self._avail]
            self.pos = self._avail
            raise Truncated
        self.pos += count
        return self._data[at : at + count]


# -- method 1: decode ----------------------------------------------------------


class _Bits1:
    """Method 1's bit reader: 16-bit LE words, low bit first, 16 bits look-ahead."""

    def __init__(self, src: _Source) -> None:
        self._src = src
        self._buf = 0
        self._count = 0

    def _refill(self) -> None:
        src = self._src
        low = src.word_byte()
        high = src.word_byte()
        self._buf = low | (high << 8) | (src.peek(0) << 16) | (src.peek(1) << 24)
        self._count = 16

    def bits(self, count: int) -> int:
        value = 0
        shift = 0
        while count:
            if not self._count:
                self._refill()
            take = min(count, self._count)
            value |= (self._buf & ((1 << take) - 1)) << shift
            self._buf >>= take
            self._count -= take
            shift += take
            count -= take
        return value

    def peek(self, count: int) -> int:
        """The next ``count`` bits without consuming them — or refilling.

        The reference decoder matches codes against the look-ahead as it stands.
        That is safe because the look-ahead always holds the unconsumed bits of
        the current word plus the next 16, and no code is longer than 15.
        """
        return self._buf & ((1 << count) - 1)

    def resync(self) -> None:
        """Refresh the look-ahead after raw bytes moved the byte position on."""
        src = self._src
        ahead = src.peek(0) | (src.peek(1) << 8) | (src.peek(2) << 16)
        kept = self._buf & ((1 << self._count) - 1)
        self._buf = ((ahead << self._count) | kept) & 0xFFFFFFFF


def _canonical_codes(depths: list[int]) -> list[tuple[int, int]]:
    """``(depth, code)`` per symbol, codes bit-reversed for a low-bit-first read.

    Assigned shortest length first and, within a length, in symbol order — the
    reference's own order, and the only one both sides can derive from lengths
    alone. The caller has checked the lengths describe a prefix code.
    """
    codes = [(0, 0)] * len(depths)
    next_code = 0
    for depth in range(1, M1_MAX_CODE_BITS + 1):
        for symbol, symbol_depth in enumerate(depths):
            if symbol_depth == depth:
                reversed_code = int(f"{next_code:0{depth}b}"[::-1], 2)
                codes[symbol] = (depth, reversed_code)
                next_code += 1
        next_code <<= 1
    return codes


class _Table:
    """One method 1 Huffman table, read from the stream."""

    def __init__(self, bits: _Bits1) -> None:
        count = bits.bits(5)
        if count > M1_TABLE_SYMBOLS:
            raise _fail(f"a table declares {count} symbols, more than 16")
        depths = [bits.bits(4) for _ in range(count)]
        # The Kraft sum, in units of 2**-15: above one whole, two codes collide.
        if (
            sum(1 << (M1_MAX_CODE_BITS - d) for d in depths if d)
            > 1 << M1_MAX_CODE_BITS
        ):
            raise _fail("code lengths over-subscribe the code space")
        self._lookup = {
            code: symbol
            for symbol, code in enumerate(_canonical_codes(depths))
            if code[0]
        }
        self._depths = sorted({d for d in depths if d})

    def value(self, bits: _Bits1) -> int:
        for depth in self._depths:
            symbol = self._lookup.get((depth, bits.peek(depth)))
            if symbol is not None:
                bits.bits(depth)
                if symbol < 2:
                    return symbol
                return bits.bits(symbol - 1) | (1 << (symbol - 1))
        raise _fail("no table code matches the bit stream")


def _unpack_1(src: _Source, target: int, out: bytearray) -> None:
    bits = _Bits1(src)
    _flags(bits.bits(1), bits.bits(1))
    while len(out) < target:
        runs = _Table(bits)
        distances = _Table(bits)
        lengths = _Table(bits)
        subchunks = bits.bits(16)
        if not subchunks:
            # The reference decoder would loop here forever without output.
            raise _fail("a chunk declares no subchunks")
        for left in range(subchunks - 1, -1, -1):
            run = runs.value(bits)
            if run:
                _room(out, run, target)
                out += src.take(run, out)
                bits.resync()
            if left:
                distance = distances.value(bits) + 1
                length = lengths.value(bits) + M1_MIN_MATCH
                _copy(out, distance, length, target)


def _flags(locked: int, keyed: int) -> None:
    # The lock bit only stops the packer re-packing a stream; unpacking ignores it.
    del locked
    if keyed:
        raise _fail("stream is encrypted, and the key is not part of the data")


def _room(out: bytearray, count: int, target: int) -> None:
    if len(out) + count > target:
        raise _fail(f"output runs past the declared {target:,} bytes")


def _copy(out: bytearray, distance: int, length: int, target: int) -> None:
    """A back-reference under both of RNC's limits: the shared reach check, then
    the declared unpacked size."""
    check_reach(out, distance, _SCHEME)
    _room(out, length, target)
    copy_back(out, distance, length)


# -- method 2: decode ----------------------------------------------------------


class _Bits2:
    """Method 2's bit reader: one byte at a time, high bit first."""

    def __init__(self, src: _Source) -> None:
        self._src = src
        self._buf = 0
        self._count = 0

    def bit(self) -> int:
        if not self._count:
            self._buf = self._src.take(1)[0]
            self._count = 8
        self._count -= 1
        return (self._buf >> self._count) & 1

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value


def _distance_2(bits: _Bits2, src: _Source) -> int:
    high = 0
    if bits.bit():
        high = bits.bit()
        if bits.bit():
            high = ((high << 1) | bits.bit()) | 4
            if not bits.bit():
                high = (high << 1) | bits.bit()
        elif not high:
            high = bits.bit() + 2
    return ((high << 8) | src.take(1)[0]) + 1


def _unpack_2(src: _Source, target: int, out: bytearray) -> None:
    bits = _Bits2(src)
    _flags(bits.bit(), bits.bit())
    while len(out) < target:
        while True:
            if not bits.bit():
                _room(out, 1, target)
                out += src.take(1)
                continue
            if bits.bit():
                if bits.bit():
                    if bits.bit():
                        length = src.take(1)[0] + 8
                        if length == 8:
                            bits.bit()  # "more chunks follow"; the size decides
                            break
                    else:
                        length = 3
                    distance = _distance_2(bits, src)
                else:
                    length = 2
                    distance = src.take(1)[0] + 1
            else:
                length = bits.bit() + 4
                if bits.bit():
                    length = ((length - 1) << 1) + bits.bit()
                if length == M2_LONG_MIN:
                    run = (bits.bits(4) << 2) + M2_RUN_MIN
                    _room(out, run, target)
                    out += src.take(run, out)
                    continue
                distance = _distance_2(bits, src)
            _copy(out, distance, length, target)


def decompress(
    data: bytes, *, method: int, partial: bool = False
) -> tuple[bytes, int, bool]:
    """Decode one RNC stream of ``method`` at ``data[0]``.

    Returns ``(output, consumed, complete)``. ``consumed`` is the header plus its
    packed size once the stream is whole. With ``partial`` a buffer that ends
    inside the stream yields what decoded before the cut, unverified; a header or
    CRC that is wrong still raises.
    """
    if len(data) < HEADER_SIZE:
        raise _fail(f"shorter than the {HEADER_SIZE}-byte header")
    if data[:3] != MAGIC:
        raise _fail("no RNC magic")
    if data[3] != method:
        raise _fail(f"method byte is {data[3]}, not {method}")
    target = int.from_bytes(data[4:8], "big")
    packed = int.from_bytes(data[8:12], "big")
    if target > MAX_OUT:
        raise _fail(f"declared size {target:,} is past the {MAX_OUT:,} cap")
    end = HEADER_SIZE + packed
    whole = len(data) >= end
    if whole:
        if crc16(data[HEADER_SIZE:end]) != int.from_bytes(data[14:16], "big"):
            raise _fail("packed data fails its CRC")
    elif not partial:
        raise _fail(f"source ends {len(data):,} bytes into a {end:,}-byte stream")

    src = _Source(data, end)
    out = bytearray()
    try:
        (_unpack_1 if method == METHOD_1 else _unpack_2)(src, target, out)
    except Truncated:
        return bytes(out), min(src.pos, len(data)), False
    if not whole:
        # The decoder reached the declared size inside what the buffer holds —
        # a stream whose packed size covers padding it never reads. The rest of
        # the stream is still missing, its CRC unchecked, so this is a prefix
        # and `consumed` stops where the buffer does.
        return bytes(out), min(end, len(data)), False
    if crc16(out) != int.from_bytes(data[12:14], "big"):
        raise _fail("unpacked data fails its CRC")
    return bytes(out), end, True


# -- compression ------------------------------------------------------------


class _Writer(InterleavedWriter):
    """The shared writer plus the leeway figure the header carries.

    ``low_first`` picks the method: 16-bit words filled from the low bit (1) or
    bytes from the high (2). Both refill lazily — a raw byte written with no word
    open goes straight out — so ``eager`` is false for either.
    """

    def __init__(self, *, word_bits: int, low_first: bool) -> None:
        super().__init__(word_bits=word_bits, low_first=low_first, eager=False)
        self.consumed = 0  # input bytes covered so far, for the leeway figure
        self.leeway = 0

    def _flush(self) -> None:
        super()._flush()
        # How far the unpacked data has run ahead of the packed data at this
        # point — the overlap an unpack into its own buffer has to allow for.
        self.leeway = max(self.leeway, self.consumed - len(self.out))

    def finish(self) -> bytes:
        if self._bits:
            if self._low_first:
                self.out.append(self._word & 0xFF)
                # A final word of eight bits or fewer is written as one byte —
                # unless raw bytes follow, which the decoder's two-byte fetch
                # would otherwise swallow.
                if self._bits > 8 or self._pending:
                    self.out.append(self._word >> 8)
            else:
                self.out.append((self._word << (8 - self._bits)) & 0xFF)
        self.out += self._pending
        return bytes(self.out)


def _symbol(value: int) -> int:
    return value if value < 2 else value.bit_length()


def _code_lengths(freq: list[int]) -> list[int]:
    """Huffman code lengths for up to 16 symbols; unused symbols get 0."""
    depths = [0] * M1_TABLE_SYMBOLS
    used = [s for s, f in enumerate(freq) if f]
    if len(used) == 1:
        depths[used[0]] = 1  # a one-symbol code still spends a bit
        return depths
    heap = [(freq[s], s, [s]) for s in used]
    heapq.heapify(heap)
    while len(heap) > 1:
        f1, t1, group1 = heapq.heappop(heap)
        f2, t2, group2 = heapq.heappop(heap)
        for s in group1 + group2:
            depths[s] += 1
        heapq.heappush(heap, (f1 + f2, min(t1, t2), group1 + group2))
    return depths


def _write_table(writer: _Writer, depths: list[int]) -> list[tuple[int, int]]:
    count = max((s + 1 for s, d in enumerate(depths) if d), default=0)
    writer.put(count, 5)
    for depth in depths[:count]:
        writer.put(depth, 4)
    return _canonical_codes(depths)


def _write_value(writer: _Writer, codes: list[tuple[int, int]], value: int) -> None:
    symbol = _symbol(value)
    depth, code = codes[symbol]
    writer.put(code, depth)
    if symbol >= 2:
        writer.put(value - (1 << (symbol - 1)), symbol - 1)


# One subchunk: (literal start, literal count, match length, match distance).
Subchunk = tuple[int, int, int, int]


def _chunks_1(data: bytes) -> list[list[Subchunk]]:
    """Method 1's parse, cut into chunks that each end on a literal run."""
    n = len(data)
    chunk_input = max(M1_CHUNK_INPUT, -(-n // MAX_CHUNKS))
    finder = MatchFinder(
        data, min_match=3, window=M1_WINDOW, max_candidates=M1_CANDIDATES
    )
    chunks: list[list[Subchunk]] = []
    current: list[Subchunk] = []
    covered = 0
    run_start, run = 0, 0
    for pos, length, candidate in parse_greedy(
        data, finder, min_match=3, max_match=M1_MAX_MATCH
    ):
        if not length:
            if run == M1_MAX_RUN:
                current.append((run_start, run, 0, 0))
                chunks.append(current)
                current, covered, run_start, run = [], 0, pos, 0
            run += 1
            covered += 1
            continue
        current.append((run_start, run, length, pos - candidate))
        covered += length
        run_start, run = pos + length, 0
        if run_start < n and (
            covered >= chunk_input or len(current) == M1_MAX_SUBCHUNKS - 1
        ):
            current.append((run_start, 0, 0, 0))
            chunks.append(current)
            current, covered = [], 0
    if n:
        current.append((run_start, run, 0, 0))
        chunks.append(current)
    return chunks


def _pack_1(data: bytes, writer: _Writer) -> int:
    chunks = _chunks_1(data)
    for chunk in chunks:
        run_freq = [0] * M1_TABLE_SYMBOLS
        distance_freq = [0] * M1_TABLE_SYMBOLS
        length_freq = [0] * M1_TABLE_SYMBOLS
        for index, (_, run, length, distance) in enumerate(chunk):
            run_freq[_symbol(run)] += 1
            if index < len(chunk) - 1:
                distance_freq[_symbol(distance - 1)] += 1
                length_freq[_symbol(length - M1_MIN_MATCH)] += 1
        run_codes = _write_table(writer, _code_lengths(run_freq))
        distance_codes = _write_table(writer, _code_lengths(distance_freq))
        length_codes = _write_table(writer, _code_lengths(length_freq))
        writer.put(len(chunk), 16)
        for index, (start, run, length, distance) in enumerate(chunk):
            _write_value(writer, run_codes, run)
            writer.consumed += run
            if run:
                writer.raw(data[start : start + run])
            if index < len(chunk) - 1:
                _write_value(writer, distance_codes, distance - 1)
                _write_value(writer, length_codes, length - M1_MIN_MATCH)
                writer.consumed += length
    return len(chunks)


def _distance_bits(distance: int) -> int:
    return M2_DISTANCE_CODES[(distance - 1) >> 8][1]


def _match_bits(length: int, distance: int) -> int:
    """What a method 2 match costs to write, raw bytes included."""
    if length == 2:
        return M2_LENGTH2_CODE[1] + 8
    head = M2_LONG_CODE[1] + 8 if length >= M2_LONG_MIN else M2_LENGTH_CODES[length][1]
    return head + _distance_bits(distance) + 8


def _ops_2(data: bytes) -> list[tuple[int, int]]:
    """Method 2's parse as ``(length, distance)`` ops by shortest path over bits.

    Distance 0 is a literal of ``length`` bytes (1, or a 12..72 run). The fixed
    bit costs make the parse a plain right-to-left shortest path, the Kosinski
    shape: the nearest 2-byte match is what the one-byte-distance form can use,
    and it is routinely not the longest match inside 4 KiB, so both are searched.
    """
    n = len(data)
    near_len, near_at = MatchFinder(
        data, min_match=2, window=M2_NEAR_WINDOW
    ).all_longest(2)
    far_len, far_at = MatchFinder(data, min_match=3, window=M2_WINDOW).all_longest(
        M2_MAX_MATCH
    )

    def options(i: int, cost: list[float]) -> Iterator[tuple[int, float, int]]:
        """Every op method 2 could write at ``i``, priced whole. The tag is the
        distance, which is ``0`` for the two literal forms."""
        yield 1, cost[i + 1] + 9, 0
        for run in range(M2_RUN_MIN, min(M2_RUN_MAX, n - i) + 1, M2_RUN_STEP):
            yield run, cost[i + run] + M2_RUN_CODE[1] + 4 + 8 * run, 0
        if near_len[i]:
            distance = i - near_at[i]
            yield 2, cost[i + 2] + _match_bits(2, distance), distance
        if far_len[i]:
            distance = i - far_at[i]
            for length in range(3, min(far_len[i], M2_LONG_MIN - 1) + 1):
                yield length, cost[i + length] + _match_bits(length, distance), distance
            if far_len[i] >= M2_LONG_MIN:
                length = far_len[i]
                yield length, cost[i + length] + _match_bits(length, distance), distance

    return parse_shortest(n, tail_cost=0, options=options)


def _pack_2(data: bytes, writer: _Writer) -> int:
    chunks = 1
    covered = 0
    at = 0
    n = len(data)
    for length, distance in _ops_2(data):
        if not distance:
            if length == 1:
                writer.put(0, 1)
            else:
                writer.put(*M2_RUN_CODE)
                writer.put((length - M2_RUN_MIN) >> 2, 4)
            writer.consumed += length
            writer.raw(data[at : at + length])
        else:
            low = bytes(((distance - 1) & 0xFF,))
            if length == 2:
                writer.put(*M2_LENGTH2_CODE)
            else:
                if length >= M2_LONG_MIN:
                    writer.put(*M2_LONG_CODE)
                    writer.raw(bytes((length - 8,)))
                else:
                    writer.put(*M2_LENGTH_CODES[length])
                writer.put(*M2_DISTANCE_CODES[(distance - 1) >> 8])
            writer.raw(low)
            writer.consumed += length
        at += length
        covered += length
        if covered >= M2_CHUNK_INPUT and at < n:
            _end_chunk(writer, more=True)
            chunks += 1
            covered = 0
    _end_chunk(writer, more=False)
    return chunks


def _end_chunk(writer: _Writer, *, more: bool) -> None:
    writer.put(*M2_LONG_CODE)
    writer.raw(b"\x00")
    writer.put(int(more), 1)


def compress(data: bytes, *, method: int) -> bytes:
    """Encode ``data`` as one RNC stream of ``method``, header and CRCs included."""
    n = len(data)
    if n > MAX_OUT:
        raise ValueError(f"input is {n:,} bytes; RNC here holds {MAX_OUT:,}")
    if method == METHOD_1:
        writer = _Writer(word_bits=16, low_first=True)
    else:
        writer = _Writer(word_bits=8, low_first=False)
    writer.put(0, 1)  # not locked
    writer.put(0, 1)  # not keyed
    chunks = (_pack_1 if method == METHOD_1 else _pack_2)(data, writer)
    if chunks > MAX_CHUNKS:
        raise ValueError(f"input needs {chunks} chunks; the header counts {MAX_CHUNKS}")
    body = writer.finish()

    # The packer's own leeway figure: the overrun measured at each word, less the
    # room the packed stream's smaller size already leaves; method 2 adds two.
    leeway = writer.leeway - (n - len(body)) if n >= len(body) else 0
    leeway = max(leeway, 0) + (2 if method == METHOD_2 else 0)

    header = bytearray(MAGIC)
    header.append(method)
    header += n.to_bytes(4, "big")
    header += len(body).to_bytes(4, "big")
    header += crc16(data).to_bytes(2, "big")
    header += crc16(body).to_bytes(2, "big")
    header.append(min(leeway, 0xFF))
    header.append(chunks)
    return bytes(header) + body


class _RncBase(PartialDecompression):
    """Both directions of one RNC method; the method byte is all that differs."""

    _method: int

    def _decode(self, data: bytes, *, partial: bool) -> tuple[bytes, int, bool]:
        return decompress(data, method=self._method, partial=partial)

    def _encode(self, data: bytes) -> bytes:
        return compress(data, method=self._method)


class Rnc1(_RncBase):
    # The header's packed size bounds the stream exactly, so a scan steps by it.
    _method = METHOD_1
    # Magic plus the method byte, which is how a scheme announces itself to the
    # Decompressed view and to Find All; the packed CRC then throws out stray
    # matches before anything is unpacked.
    signature = MAGIC + bytes((METHOD_1,))
    info = PluginInfo(
        "rnc1", "RNC 1 (Rob Northen LZ + Huffman)", Stage.COMPRESSION, "Generic"
    )


class Rnc2(_RncBase):
    _method = METHOD_2
    signature = MAGIC + bytes((METHOD_2,))
    info = PluginInfo(
        "rnc2", "RNC 2 (Rob Northen fast LZ)", Stage.COMPRESSION, "Generic"
    )
