"""The SNES command-stream LZ family: LZ1 (Zelda 3) and LZ2 (SMW, YI) codecs.

A compressed stream is a sequence of commands, each a header byte (or two)
followed by its payload, terminated by a ``0xFF`` header. Header layout::

    short form:  CCCLLLLL            command = bits 7..5 (not 111),
                                     length  = bits 4..0 + 1        (1..32)
    long form:   111CCCLL LLLLLLLL   command = bits 4..2,
                                     length  = 10 bits + 1          (1..1024)

Commands (``length`` output bytes each):

    000  literal        copy ``length`` source bytes verbatim
    001  byte fill      repeat the next source byte
    010  word fill      alternate the next two source bytes (a,b,a,b,…)
    011  increasing     next source byte, then +1, +2, … (mod 256)
    1xx  backreference  copy from an **absolute** 16-bit offset into the output
                        produced so far (overlap allowed — a forward-copy RLE).
                        All four high commands decode identically.

The two family members differ only in the backreference offset's byte order:
**LZ1 is little-endian, LZ2 big-endian**. Everything else is shared, so both
plugins parameterise one engine.

Any stream that round-trips is valid, so the parse — which command to emit where
— is a free choice the format does not constrain, and there are two worth making.
:func:`compress` reproduces the parse the original SNES tooling used: a single
left-to-right pass in which runs pre-empt every other command, which re-encodes a
decoded cart structure to its original bytes and so leaves an unedited structure
byte-identical (and certain to still fit its slot). :func:`compress_improved`
instead parses by shortest path — the cheapest encoding of the whole remaining
structure, not the command that looks best where it stands — and gives up
byte-exactness for ~13% smaller streams.

Both emit only backreference command ``100``: the decoder treats 5/6/7 as
aliases, and command 7 in long form collides with the ``0xFF`` terminator
(``111 111 11``), so avoiding them keeps every emitted header unambiguous.
"""

from __future__ import annotations

from mapchar.plugins.base import PartialDecompression, PluginInfo, Stage
from mapchar.plugins.builtins.compression._limits import MAX_BANK, stream_error
from mapchar.plugins.builtins.compression._lz import MatchFinder, copy_from

_SCHEME = "LZ"
_TERMINATOR = 0xFF
_MAX_SHORT = 32
_MAX_LONG = 1024

_OP_LITERAL = 0x00
_OP_FILL = 0x20
_OP_WORD_FILL = 0x40
_OP_INCREASING = 0x60
_OP_BACKREF = 0x80

# Shortest-path tuning: a run or backref shorter than 3 never beats literals, and
# the candidate cap bounds pathological inputs only — deepening it to 256 costs
# a third more time and buys 0.3%, to 1024 another 60% for 0.1% more.
_MIN_MATCH = 3
_MAX_CHAIN = 64

# The original parse's thresholds and caps, every one of them load-bearing for
# byte-exactness. The caps are not uniform and the reason is not known — a run
# past its cap
# is emitted at the cap and re-scanned from there, which is what puts a stray
# literal between two word runs in the shipped data.
_ORIG_MIN_FILL = 3
_ORIG_MIN_WORD = 4
_ORIG_MIN_INC = 3
_ORIG_MIN_BACKREF = 4
_ORIG_MAX_FILL = 1024
_ORIG_MAX_WORD = 1023
_ORIG_MAX_INC = 1024
_ORIG_MAX_BACKREF = 512
# The one number the shipped data cannot pin down — its longest literal run is
# 386 bytes, so any cap at or above that agrees with it. The format maximum is
# the safe reading: too low a cap would split runs the original never split.
_ORIG_MAX_LITERAL = 1024

# Stands in where a command cannot be written at all; its cost is infinite, so it
# never reaches the emitter.
_NO_CHOICE = (_OP_LITERAL, 0, 0)


def decompress(
    data: bytes, *, big_endian_offsets: bool, partial: bool = False
) -> tuple[bytes, int]:
    """Decode one compressed structure from the start of ``data``.

    Returns ``(output, consumed)``, ``consumed`` counting the compressed bytes
    through the terminator, so a caller handing in an over-read buffer learns the
    structure's true extent. Trailing bytes after the terminator are never touched.

    With ``partial``, for a *bounded* buffer that may cut the structure
    short, running out of source is not an error: the prefix decoded so far comes
    back, finishing as much of the current command as the buffer allows. Reaching
    the one-bank output cap is a short read of the same kind
    (:mod:`~mapchar.plugins.builtins.compression._limits`). Structural
    corruption — a backreference into unwritten output — raises either way, which
    is what lets a window preview tell "a structure continues past the window"
    from "this is not a structure at all".
    """
    out = bytearray()
    n = len(data)
    i = 0

    def truncated(reason: str) -> tuple[bytes, int]:
        if partial:
            return bytes(out), n
        raise stream_error(_SCHEME, reason)

    while True:
        if i >= n:
            return truncated("source exhausted before the 0xFF terminator")
        cmd = data[i]
        i += 1
        if cmd == _TERMINATOR:
            return bytes(out), i
        if (cmd & 0xE0) == 0xE0:  # long form
            if i >= n:
                return truncated("source exhausted inside a long-form header")
            length = (((cmd & 0x03) << 8) | data[i]) + 1
            i += 1
            op = (cmd << 3) & 0xE0
        else:
            length = (cmd & 0x1F) + 1
            op = cmd & 0xE0
        if len(out) + length > MAX_BANK:
            return truncated(f"output exceeds the {MAX_BANK:#x}-byte cap")

        if op == _OP_LITERAL:
            if i + length > n:
                out += data[i:n]
                return truncated("source exhausted inside a literal run")
            out += data[i : i + length]
            i += length
        elif op == _OP_FILL:
            if i >= n:
                return truncated("source exhausted reading a fill byte")
            out += data[i : i + 1] * length
            i += 1
        elif op == _OP_WORD_FILL:
            if i + 2 > n:
                return truncated("source exhausted reading a word-fill pair")
            pair = data[i : i + 2]
            i += 2
            out += (pair * ((length + 1) // 2))[:length]
        elif op == _OP_INCREASING:
            if i >= n:
                return truncated("source exhausted reading an increasing-fill byte")
            v = data[i]
            i += 1
            out += bytes((v + k) & 0xFF for k in range(length))
        else:  # backreference (all four high commands)
            if i + 2 > n:
                return truncated("source exhausted reading a backreference offset")
            if big_endian_offsets:
                off = (data[i] << 8) | data[i + 1]
            else:
                off = data[i] | (data[i + 1] << 8)
            i += 2
            if off >= len(out):
                raise stream_error(
                    _SCHEME,
                    f"backreference into unwritten output ({off:#x} >= {len(out):#x})",
                )
            # Overlap-aware: a copy reaching past `len(out)` re-reads bytes this
            # command just produced, which is the format's run-extension idiom.
            copy_from(out, off, length)


def _check_input(data: bytes) -> bytes | None:
    """Both parses' entry rule, and the empty structure when there is nothing to
    pack: a backreference offset is 16 bits absolute, so a structure is one bank.
    """
    n = len(data)
    if n > MAX_BANK:
        raise ValueError(
            f"data is {n:#x} bytes; LZ structures cap at {MAX_BANK:#x} (one 64 KB bank)"
        )
    return bytes((_TERMINATOR,)) if n == 0 else None


def _emit_header(out: bytearray, op: int, length: int) -> None:
    if length <= _MAX_SHORT:
        out.append(op | (length - 1))
    else:
        encoded = length - 1  # 0..1023
        out.append(0xE0 | ((op >> 3) & 0x1C) | (encoded >> 8))
        out.append(encoded & 0xFF)


def _emit_offset(out: bytearray, field: int, big_endian_offsets: bool) -> None:
    """The backreference's absolute 16-bit offset — the one thing LZ1 and LZ2
    disagree about."""
    hi, lo = (field >> 8) & 0xFF, field & 0xFF
    out += bytes((hi, lo) if big_endian_offsets else (lo, hi))


def _emit_op(
    out: bytearray,
    data: bytes,
    i: int,
    op: int,
    length: int,
    field: int = 0,
    *,
    big_endian_offsets: bool,
) -> None:
    """One whole command covering ``data[i : i + length]``: header, then payload.

    What the payload is follows from ``op`` alone, so both parses emit through
    this and neither can disagree with the decoder about it.
    """
    _emit_header(out, op, length)
    if op == _OP_LITERAL:
        out += data[i : i + length]
    elif op == _OP_FILL or op == _OP_INCREASING:
        out.append(data[i])
    elif op == _OP_WORD_FILL:
        out += data[i : i + 2]
    else:
        _emit_offset(out, field, big_endian_offsets)


def _run_tables(
    data: bytes, *, caps: tuple[int, int, int], wrap: bool
) -> tuple[list[int], list[int], list[int]]:
    """How far the fill, increasing and alternating patterns reach from each
    position — the three commands whose reach is a property of the bytes alone,
    so it is worth knowing everywhere at once rather than re-measuring per parse
    step.

    ``caps`` is the ``(fill, increasing, alternating)`` maximum each command may
    be emitted at, which differs between the two parses. ``wrap`` says whether an
    increasing run may cross ``$FF`` back to ``$00``: the decoder always does, but
    the original encoder never emitted a run that needed it, and those bytes fall
    through to literals — so reproducing its stream means stopping there.

    Both tables run to ``n + 1`` so a position may ask about the one past its own
    without a bounds test; nothing can start there, so the extra entry stays 1.
    """
    n = len(data)
    max_fill, max_inc, max_alt = caps
    fill = [1] * (n + 1)
    inc = [1] * (n + 1)
    alt = [1] * (n + 1)
    for i in range(n - 1, -1, -1):
        nxt = i + 1
        if nxt < n:
            if data[nxt] == data[i]:
                fill[i] = min(fill[nxt] + 1, max_fill)
            step = (data[i] + 1) & 0xFF if wrap else data[i] + 1
            if data[nxt] == step:
                inc[i] = min(inc[nxt] + 1, max_inc)
        # a,b,a,b,… continues exactly when data[i+2] repeats data[i]; the tail
        # from i+1 is the same shape with the pair swapped, so its length carries.
        alt[i] = (
            min(alt[nxt] + 1, max_alt)
            if i + 2 < n and data[i + 2] == data[i]
            else min(2, n - i)
        )
    return fill, inc, alt


def _original_runs(data: bytes) -> tuple[list[int], list[int], list[int], list[int]]:
    """Each run command's reach at every position under the original parse's
    pre-emption rules, plus where the next run of any kind starts.

    Returns ``(fill, word, inc, next_run)``. The first three are the length the
    command would be *emitted* with at that position — below its minimum it does
    not fire there — and ``next_run[i]`` is the first position from ``i`` on where
    some run fires, which is where a backreference has to stop.

    Pre-emption is what the clipping over :func:`_run_tables` encodes: a fill run
    is always maximal, a word run yields the boundary byte to a fill starting
    inside it, and an increasing run yields to either. One backward pass does the
    lot, since each clip needs only the answers already computed for ``i + 1``.
    """
    n = len(data)
    fill, inc_raw, word_raw = _run_tables(
        data, caps=(_ORIG_MAX_FILL, _ORIG_MAX_INC, _ORIG_MAX_WORD), wrap=False
    )
    word = [1] * n
    inc = [1] * n
    # Length n + 1: position n - 1 asks about n, where no run can start.
    next_fill = [n] * (n + 1)
    next_word = [n] * (n + 1)
    next_run = [n] * (n + 1)
    for i in range(n - 1, -1, -1):
        nxt = i + 1
        next_fill[i] = i if fill[i] >= _ORIG_MIN_FILL else next_fill[nxt]
        word[i] = min(word_raw[i], next_fill[nxt] - i)
        next_word[i] = i if word[i] >= _ORIG_MIN_WORD else next_word[nxt]
        inc[i] = min(inc_raw[i], next_fill[nxt] - i, next_word[nxt] - i)
        next_run[i] = (
            i
            if fill[i] >= _ORIG_MIN_FILL
            or word[i] >= _ORIG_MIN_WORD
            or inc[i] >= _ORIG_MIN_INC
            else next_run[nxt]
        )
    return fill, word, inc, next_run


def compress(data: bytes, *, big_endian_offsets: bool) -> bytes:
    """Encode ``data`` (≤ 64 KB) as one compressed structure, the original parse.

    A single left-to-right pass with no lookahead and no cost comparison: at each
    position the first command that fires wins — fill, word fill, increasing fill,
    then the longest backreference — and a byte nothing fires on joins a pending
    literal run. Runs pre-empt everything (:func:`_original_runs`), which is what
    makes the parse reproducible at all, and what turns a 22-byte match in the
    shipped data into backref/word/backref: the match was not truncated for cost,
    a run began.

    Re-encoding a structure decoded from a Yoshi's Island cart blob reproduces
    that blob byte for byte, so an unedited structure saves back unchanged and
    certainly still fits its slot. The rules were recovered from that one game's
    data; on a title packed by other tooling this is simply a mediocre
    compressor, never an invalid one, and :func:`compress_improved` is the
    ~13%-smaller alternative.
    """
    empty = _check_input(data)
    if empty is not None:
        return empty
    n = len(data)

    fill, word, inc, next_run = _original_runs(data)
    # Ties go to the *earliest* offset, so the chain is walked oldest-first and
    # uncapped: unlike the shortest path's search, dropping candidates here would
    # change which stream comes out, not just how long finding it takes.
    finder = MatchFinder(
        data, min_match=_ORIG_MIN_BACKREF, window=None, oldest_first=True
    )

    out = bytearray()
    indexed = 0
    literal_start = -1

    def flush_literals(end: int) -> None:
        nonlocal literal_start
        if literal_start < 0:
            return
        pos = literal_start
        while pos < end:
            length = min(end - pos, _ORIG_MAX_LITERAL)
            _emit_op(
                out,
                data,
                pos,
                _OP_LITERAL,
                length,
                big_endian_offsets=big_endian_offsets,
            )
            pos += length
        literal_start = -1

    i = 0
    while i < n:
        field = 0
        if fill[i] >= _ORIG_MIN_FILL:
            op, length = _OP_FILL, fill[i]
        elif word[i] >= _ORIG_MIN_WORD:
            op, length = _OP_WORD_FILL, word[i]
        elif inc[i] >= _ORIG_MIN_INC:
            op, length = _OP_INCREASING, inc[i]
        else:
            op, length = _OP_BACKREF, 0
            limit = min(_ORIG_MAX_BACKREF, n - i, next_run[i + 1] - i)
            if limit >= _ORIG_MIN_BACKREF:
                # Index everything the parse stepped over, so a match may start
                # inside an earlier command as it does in the shipped streams.
                finder.add_run(indexed, i)
                indexed = i
                # The candidate position *is* the field: this back-reference is
                # absolute within the structure, not a distance.
                length, field = finder.longest(i, limit)
            if length < _ORIG_MIN_BACKREF:
                if literal_start < 0:
                    literal_start = i
                i += 1
                continue

        flush_literals(i)
        _emit_op(out, data, i, op, length, field, big_endian_offsets=big_endian_offsets)
        i += length

    flush_literals(n)
    out.append(_TERMINATOR)
    return bytes(out)


def compress_improved(data: bytes, *, big_endian_offsets: bool) -> bytes:
    """Encode ``data`` (≤ 64 KB) as one compressed structure, as small as it goes.

    The parse is a shortest path, not a greedy walk: ``cost[i]`` is the fewest
    compressed bytes that can encode ``data[i:]``, solved right-to-left, so every
    command and every length is priced against what the rest of the structure
    then costs. A greedy parse has to guess — the command that covers the most
    bytes here can leave the next position straddling a run it would otherwise
    have coded whole — and on Yoshi's Island's blobs guessing costs ~1.4%.
    """
    empty = _check_input(data)
    if empty is not None:
        return empty
    n = len(data)

    fill, inc, alt = _run_tables(
        data, caps=(_MAX_LONG, _MAX_LONG, _MAX_LONG), wrap=True
    )
    # No distance window: the offset is *absolute* within the structure, so every
    # earlier position is addressable. Bounding the scan to the newest
    # _MAX_CHAIN candidates keeps a worst-case input from going quadratic.
    match_len, match_at = MatchFinder(
        data, min_match=_MIN_MATCH, window=None, max_candidates=_MAX_CHAIN
    ).all_longest(_MAX_LONG)

    inf = float("inf")
    cost: list[float] = [inf] * (n + 1)
    # cost[j] + j, which is what a literal run reaching j is worth: its payload is
    # its length, so folding that in turns "cheapest literal length" into a plain
    # minimum over a window rather than a scan that has to weigh each length.
    reach: list[float] = [inf] * (n + 1)
    choice: list[tuple[int, int, int]] = [(_OP_LITERAL, 1, 0)] * (n + 1)
    cost[n] = 0
    reach[n] = n

    def priced(
        i: int, op: int, reach_len: int, payload: int, field: int
    ) -> tuple[float, tuple[int, int, int]]:
        """Cheapest way to write ``op`` at ``i``, in either header form.

        Only the longest length each form allows is priced. A shorter one can in
        principle win — ``cost`` is non-increasing apart from the odd single byte,
        where dropping a byte off the front forces an extra header — but scanning
        every length for that costs a third more time and recovers 0.03%.
        """
        if reach_len < _MIN_MATCH:
            return inf, _NO_CHOICE
        length = reach_len if reach_len < _MAX_SHORT else _MAX_SHORT
        best = cost[i + length] + 1 + payload
        pick = (op, length, field)
        if reach_len > _MAX_SHORT:
            length = reach_len if reach_len < _MAX_LONG else _MAX_LONG
            value = cost[i + length] + 2 + payload
            if value < best:
                best = value
                pick = (op, length, field)
        return best, pick

    for i in range(n - 1, -1, -1):
        # Literals first: they always apply, so they seed the minimum.
        stop = min(i + _MAX_SHORT, n)
        value = min(reach[i + 1 : stop + 1])
        best = value + 1 - i
        best_choice = (_OP_LITERAL, reach.index(value, i + 1, stop + 1) - i, 0)
        if n - i > _MAX_SHORT:
            stop = min(i + _MAX_LONG, n)
            value = min(reach[i + 33 : stop + 1])
            if value + 2 - i < best:
                best = value + 2 - i
                best_choice = (_OP_LITERAL, reach.index(value, i + 33, stop + 1) - i, 0)

        for value, pick in (
            priced(i, _OP_FILL, fill[i], 1, 0),
            priced(i, _OP_INCREASING, inc[i], 1, 0),
            # Equal bytes are the plain fill's job, never the word fill's.
            priced(
                i,
                _OP_WORD_FILL,
                alt[i] if i + 1 < n and data[i] != data[i + 1] else 0,
                2,
                0,
            ),
            priced(i, _OP_BACKREF, match_len[i], 2, match_at[i]),
        ):
            if value < best:
                best = value
                best_choice = pick

        cost[i] = best
        reach[i] = best + i
        choice[i] = best_choice

    out = bytearray()
    i = 0
    while i < n:
        op, length, field = choice[i]
        _emit_op(out, data, i, op, length, field, big_endian_offsets=big_endian_offsets)
        i += length
    out.append(_TERMINATOR)
    return bytes(out)


class _LzBase(PartialDecompression):
    """Both directions of one LZ variant; ``_big_endian`` is all that differs.

    ``_improved`` picks the parse, which only a save-back can tell apart: both
    write the same format, so a structure written by either decodes identically.
    """

    _big_endian: bool
    _improved = False

    def _decode(self, data: bytes, *, partial: bool) -> tuple[bytes, int, bool]:
        # Strict first: reaching the terminator means the structure's true end is
        # known. Fall back to a best-effort partial decode only when the caller
        # said the buffer may cut the structure short.
        try:
            out, consumed = decompress(data, big_endian_offsets=self._big_endian)
        except ValueError:
            if not partial:
                raise
        else:
            return out, consumed, True
        out, consumed = decompress(
            data, big_endian_offsets=self._big_endian, partial=True
        )
        return out, consumed, False

    def _encode(self, data: bytes) -> bytes:
        pack = compress_improved if self._improved else compress
        return pack(data, big_endian_offsets=self._big_endian)


class Lz1(_LzBase):
    _big_endian = False
    info = PluginInfo("lz1", "LZ1 (Zelda 3)", Stage.COMPRESSION, "Nintendo")


class Lz1Improved(Lz1):
    """The shortest-path parse over LZ1's little-endian offsets. Whether LZ1
    titles were packed by the same tooling is unmeasured, so this is the one to
    reach for wherever the default turns out not to reproduce a blob."""

    _improved = True
    info = PluginInfo(
        "lz1_improved", "LZ1 improved (Zelda 3)", Stage.COMPRESSION, "Nintendo"
    )


class Lz2(_LzBase):
    _big_endian = True
    info = PluginInfo("lz2", "LZ2 (SMW, Yoshi's Island)", Stage.COMPRESSION, "Nintendo")


class Lz2Improved(Lz2):
    """Same stream, a smaller parse — decoding is identical, so the two are
    interchangeable on read and only a save-back tells them apart. Worth ~13% on
    Yoshi's Island data, at the cost of an unedited structure no longer
    re-saving to its original bytes."""

    _improved = True
    info = PluginInfo(
        "lz2_improved",
        "LZ2 improved (SMW, Yoshi's Island)",
        Stage.COMPRESSION,
        "Nintendo",
    )
