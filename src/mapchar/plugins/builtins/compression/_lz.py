"""The match search every LZ compressor here runs, once — and its decode-side twin.

Every scheme here that encodes back-references — the LZSS family, PRS, Kosinski
and the command-byte LZ — differs entirely in how a match is *written* and not at
all in how one is *found*. The finding half is what lives here: an index from a
fixed-width byte prefix to the recent positions that share it, a bounded walk of
that chain newest-first, and an overlap-aware length count.

The same overlap has to be *reproduced* on the way back out, so :func:`copy_from`
and :func:`copy_back` live here too — the one piece of a decoder that is genuinely
common to every scheme, since a back-reference is the only op they all share —
along with the reach check every one of them makes first
(:func:`copy_back_checked`) and the :class:`Truncated` a bounded buffer raises.
Several also frame their ops behind a control byte or a word of selectors over
one greedy parse, which is :class:`ControlBits`, :class:`FlagGroup`,
:class:`InterleavedWriter` and :func:`parse_greedy`.

**Overlap is the part worth stating.** A match may legally reach past the position
being encoded, into bytes the decoder has not produced yet, because every one of
these decoders copies a byte at a time — so the source repeats with period
``distance`` and :meth:`MatchFinder.match_length` counts it modulo that period. A
length routine that stopped at ``at`` would silently give up the run-length
encoding these formats get for free.

What stays with each scheme is what only it knows: which distances it can reach,
what a match costs to write, and whether the longest match is the one it wants —
PRS deliberately prefers a nearer, shorter match when the cheaper op more than
pays for the bytes given up. So :meth:`longest` is offered for the schemes that
want length alone, :meth:`candidates` for the one that scores its own, and
:meth:`all_longest` for the one that parses by shortest path and therefore has to
price every position before it knows which it will use.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TypeVar

from mapchar.plugins.builtins.compression._limits import stream_error

Op = TypeVar("Op")
"""Whatever a scheme needs to remember about an op its parse picked."""

# How many recent positions sharing a prefix a scheme tests by default. Highly
# repetitive data piles up thousands, and past the newest few dozen the extra
# candidates almost never yield a longer match — so the cap bounds a pathological
# input rather than costing anything on real data.
DEFAULT_CANDIDATES = 96


def copy_from(out: bytearray, start: int, length: int) -> None:
    """Append ``length`` bytes of ``out`` from ``start``, self-overlap included.

    The decode side of the module docstring's overlap rule: a copy may reach past
    the end of ``out`` and re-read the bytes it is itself writing, so the source
    repeats with period ``len(out) - start``. That is not a corner case — it is how
    every one of these formats run-length-encodes a fill — and a bulk copy is wrong
    for exactly the data that compresses best.

    A copy that *cannot* overlap takes the slice, which is where an ordinary decode
    spends most of its time; only the overlapping ones pay for the byte loop.

    ``start`` is the caller's to validate; :func:`copy_back_checked` is the
    back-reference form with that validation already on it.
    """
    if start + length <= len(out):  # no self-overlap - copy in one go
        out += out[start : start + length]
    else:
        for k in range(length):
            out.append(out[start + k])


def copy_back(out: bytearray, distance: int, length: int) -> None:
    """:func:`copy_from` addressed as a back-reference names its source."""
    copy_from(out, len(out) - distance, length)


def check_reach(
    out: bytearray, distance: int, scheme: str, what: str = "match"
) -> None:
    """Refuse a back-reference naming a byte the output does not have yet.

    Corruption rather than a short buffer: the stream named a source byte that
    was never written, which no amount of further input would supply. Every
    decoder here tests it before copying, and the ones that word it alike word it
    here. ``what`` names the op that asked, for the formats with more than one
    back-reference form.
    """
    if distance > len(out):
        raise stream_error(
            scheme,
            f"{what} reaches {distance:,} bytes back into {len(out):,} bytes of output",
        )


def copy_back_checked(
    out: bytearray, distance: int, length: int, scheme: str, what: str = "match"
) -> None:
    """:func:`copy_back` behind :func:`check_reach`, which is the whole of what a
    decoder does with a back-reference it has just read."""
    check_reach(out, distance, scheme, what)
    copy_back(out, distance, length)


class Truncated(ValueError):
    """The buffer ended inside the stream — recoverable only under ``partial``.

    Not corruption: the bytes read so far were a stream, there are simply no more
    of them. A decoder raises it from wherever it ran out and words the stream
    error once, at the top, where it knows whether a partial decode was asked
    for. A :class:`ValueError` base so an escapee still reads as a stream error.
    """


class MatchFinder:
    """A prefix index over one buffer, for finding back-references in it.

    Positions are added as the parse passes them (:meth:`add`, :meth:`add_run`),
    never in advance: a candidate has to be a position the decoder will already
    have produced, and indexing ahead of the cursor would offer matches that
    cannot be written.

    ``window`` is how far back the scheme can reach — the ring size, the largest
    encodable distance — or ``None`` where any earlier position is addressable, as
    it is for a format whose back-reference is an absolute offset within a bank.

    ``oldest_first`` reverses the walk and disables the cap, for the one caller
    that has to reproduce another encoder's choice rather than make its own: the
    byte-exact command-LZ parse breaks ties on the *first* occurrence of a match,
    so both the newest-first order and the cap — which drops the oldest
    candidates — would change its output rather than merely its search time.
    """

    __slots__ = ("_cap", "_data", "_index", "_min_match", "_n", "_oldest", "_window")

    def __init__(
        self,
        data: bytes,
        *,
        min_match: int,
        window: int | None = None,
        max_candidates: int = DEFAULT_CANDIDATES,
        oldest_first: bool = False,
    ) -> None:
        self._data = data
        self._n = len(data)
        self._min_match = min_match
        self._window = window
        self._cap = max_candidates
        self._oldest = oldest_first
        self._index: dict[bytes, list[int]] = {}

    def add(self, pos: int) -> None:
        """Index ``pos`` as a candidate for later positions.

        A bucket is trimmed to the newest ``max_candidates`` once it has grown to
        twice that, rather than on every insert: the walk only ever looks at the
        newest ones anyway, so trimming in batches keeps a run of identical bytes
        from turning each insert into a list copy.
        """
        if pos + self._min_match > self._n:
            return
        bucket = self._index.setdefault(self._data[pos : pos + self._min_match], [])
        bucket.append(pos)
        if not self._oldest and len(bucket) > self._cap * 2:
            del bucket[: -self._cap]

    def add_run(self, start: int, end: int) -> None:
        """Index every position in ``[start, end)`` — the interior of a match.

        A match is emitted whole, so the positions it covered are stepped over by
        the parse and would otherwise never be indexed, leaving a hole in the
        history exactly where the data is most repetitive.
        """
        for pos in range(start, end):
            self.add(pos)

    def candidates(self, pos: int) -> Iterator[int]:
        """Reachable earlier positions sharing ``pos``'s prefix, newest first.

        Newest first because a nearer match is never worse — no scheme here pays
        *more* for a shorter distance — so the walk can stop at the first
        out-of-window candidate rather than filtering the whole chain. Under
        ``oldest_first`` the whole chain is walked in insertion order instead; see
        the class docstring for why that caller cannot take either shortcut.
        """
        bucket = self._index.get(self._data[pos : pos + self._min_match])
        if not bucket:
            return
        cutoff = -1 if self._window is None else pos - self._window
        if self._oldest:
            # No early exit is available walking forwards, so an out-of-window
            # candidate is skipped rather than terminating the walk.
            for candidate in bucket:
                if candidate >= cutoff:
                    yield candidate
            return
        for candidate in reversed(bucket[-self._cap :]):
            if candidate < cutoff:
                return  # the rest are older still — out of reach
            yield candidate

    def match_length(self, at: int, candidate: int, limit: int) -> int:
        """Length of the match at ``candidate``, counting legal self-overlap.

        See the module docstring: the copy may run past ``at``, so the source
        repeats with period ``at - candidate``.

        This is the innermost loop of every compress here, so the way to make it
        cheap is not to enter it: :meth:`can_reach` rules a candidate out on one
        comparison, and both callers use it before measuring.
        """
        data = self._data
        distance = at - candidate
        length = 0
        while length < limit:
            if data[at + length] != data[candidate + length % distance]:
                break
            length += 1
        return length

    def can_reach(self, at: int, candidate: int, length: int) -> bool:
        """Whether the match at ``candidate`` could be ``length`` bytes long.

        Tests the single byte a match that long would have to end on. A candidate
        that fails cannot reach ``length`` however well its front matches, so it
        never needs measuring — and over repetitive data, where a chain is long
        and most of it is beaten by the nearest few, that is what the search
        costs instead of a :meth:`match_length` per candidate.

        Necessary, not sufficient: the bytes between may still differ. It is a
        filter to put in front of the measurement, never a substitute for it.
        """
        if length <= 0 or at + length > self._n:
            return False
        distance = at - candidate
        offset = length - 1
        # Where the source is *reading* at that offset — past `distance` the copy
        # has wrapped back into its own output (see the module docstring).
        if offset >= distance:
            offset %= distance
        return self._data[candidate + offset] == self._data[at + length - 1]

    def all_longest(self, limit: int) -> tuple[list[int], list[int]]:
        """The longest match at *every* position, as parallel length/candidate lists.

        A greedy parse only asks about the positions it lands on, so it can index
        as it walks; a shortest-path parse has to price a match at every position
        before it knows which ones it will use, which is what this answers in one
        pass. Positions are still indexed in order, so no candidate is offered
        before the decoder would have produced it.

        The pass is seeded rather than restarted: a match of ``L`` at ``pos`` from
        candidate ``c`` means ``c + 1`` matches at least ``L - 1`` at ``pos + 1``,
        the same bytes with both ends shifted one on. Extending that seed by
        direct comparison first often reaches ``limit`` outright, and then the
        chain needs no walk at all — which is exactly the input that makes a chain
        long and every candidate on it a tie (a long fill, a repeating block).

        Candidates are positions, as :meth:`longest` returns them — never
        distances, which is what each scheme subtracts them into.

        Lengths below ``min_match`` come back as ``0``, candidate ``0``: too short
        to be worth writing, and no caller should be able to mistake one for
        usable.
        """
        data = self._data
        n = self._n
        min_match = self._min_match
        lengths = [0] * n
        candidates = [0] * n
        seed_len = 0
        seed_at = 0
        for pos in range(n):
            room = n - pos
            cap = limit if limit < room else room
            best_len, best_at = 0, 0
            if cap >= min_match:
                if seed_len > min_match and (
                    self._window is None or pos - (seed_at + 1) <= self._window
                ):
                    candidate = seed_at + 1
                    length = seed_len - 1
                    while (
                        length < cap and data[pos + length] == data[candidate + length]
                    ):
                        length += 1
                    best_len, best_at = length, candidate
                if best_len < cap:
                    for candidate in self.candidates(pos):
                        if (
                            best_len
                            and data[candidate + best_len] != data[pos + best_len]
                        ):
                            continue  # cannot reach best_len + 1, so cannot win
                        length = 0
                        while (
                            length < cap
                            and data[pos + length] == data[candidate + length]
                        ):
                            length += 1
                        if length > best_len:
                            best_len, best_at = length, candidate
                            if best_len == cap:
                                break
            if best_len >= min_match:
                lengths[pos] = best_len
                candidates[pos] = best_at
            seed_len, seed_at = best_len, best_at
            self.add(pos)
        return lengths, candidates

    def longest(self, pos: int, limit: int, min_distance: int = 1) -> tuple[int, int]:
        """The longest reachable match at ``pos``, as ``(length, candidate)``.

        ``(0, -1)`` when there is none worth having — fewer than ``min_match``
        bytes left to match, or no reachable candidate. The walk stops early on a
        match that fills ``limit``, since nothing later in the chain can beat it.

        ``min_distance`` is the nearest distance the *scheme* can write, which is
        not always the nearest one the index offers: candidates arrive nearest
        first, and a biased distance field cannot name the positions below its
        bias — the SLZ reference stores ``distance - 3``, and the VRAM-safe BIOS
        LZ77 call rejects the stored 0 that distance 1 would encode. Skipping them
        here rather than at the call site is also what keeps them from being
        *measured*, which a call site filtering the result cannot do.

        Only a candidate that could beat the best so far is measured: beating it
        means reaching ``best_len + 1`` bytes, which is the one-byte test
        :meth:`can_reach` makes — written out here rather than called because
        this loop runs tens of times per input byte and the call is a large
        share of what is left once the measuring is gone. The bounds ``can_reach``
        guards are given here: ``best_len < limit <= n - pos`` throughout.
        """
        if limit < self._min_match:
            return 0, -1
        data = self._data
        best_len, best_at = 0, -1
        for candidate in self.candidates(pos):
            distance = pos - candidate
            if distance < min_distance:
                continue
            if best_len:
                offset = best_len if best_len < distance else best_len % distance
                if data[candidate + offset] != data[pos + best_len]:
                    continue
            length = self.match_length(pos, candidate, limit)
            if length > best_len:
                best_len, best_at = length, candidate
                if best_len == limit:
                    break
        return best_len, best_at


def parse_greedy(
    data: bytes,
    finder: MatchFinder,
    *,
    min_match: int,
    max_match: int,
    min_distance: int = 1,
) -> Iterator[tuple[int, int, int]]:
    """The greedy parse with a one-step lazy deferral, as a stream of decisions.

    Yields one ``(pos, length, candidate)`` per op in output order. ``length`` is
    ``0`` for a literal at ``pos`` — never a match too short to write — so a caller
    tests that alone and never re-derives the decision.

    The deferral is what a plain longest-match walk gets wrong: a match here is
    worth taking only if the next position does not start a strictly longer one,
    because the literal that displaces it then buys more than it costs. It is
    tested only where it can pay — a match already at ``max_match`` cannot be
    beaten, and the last byte has no next position.

    ``finder`` is fed as the parse walks, never in advance: ``pos`` is indexed
    before the lookahead reads it, and the interior of a taken match is indexed
    behind it (a match is emitted whole, so those positions are stepped over and
    would otherwise leave a hole exactly where the data is most repetitive).
    """
    n = len(data)
    pos = 0
    while pos < n:
        limit = max_match if max_match < n - pos else n - pos
        length, candidate = finder.longest(pos, limit, min_distance)
        finder.add(pos)  # index it before any lookahead reads it
        if min_match <= length < max_match and pos + 1 < n:
            next_limit = max_match if max_match < n - pos - 1 else n - pos - 1
            next_len, _ = finder.longest(pos + 1, next_limit, min_distance)
            if next_len > length:
                length = 0
        if length >= min_match:
            finder.add_run(pos + 1, pos + length)
            yield pos, length, candidate
            pos += length
        else:
            yield pos, 0, -1
            pos += 1


def parse_shortest(
    n: int,
    *,
    tail_cost: float,
    options: Callable[[int, list[float]], Iterator[tuple[int, float, Op]]],
) -> list[tuple[int, Op]]:
    """The right-to-left shortest-path parse, as the ops it chose in output order.

    ``cost[i]`` is the cheapest encoding of the input from ``i`` on, solved from
    ``n`` down to ``0``, so every op is priced against what the rest of the input
    then costs rather than against what it covers here. That is the difference
    from :func:`parse_greedy`, and it is worth the pass for the schemes with
    several forms of one op: the longest match is frequently the expensive form,
    and two cheap ops beat one dear one often enough to matter.

    ``options(i, cost)`` yields one ``(length, cost_here, tag)`` per op the scheme
    could write at ``i``, where ``cost_here`` is the whole cost of that op *plus*
    the tail it leaves — the scheme reads ``cost[i + length]`` itself, being the
    only one that knows what its ops cost. Ties go to whichever came out of
    ``options`` first, so the order it yields in is part of what it decides, and
    it has to offer at least one op at every position (the literal, for every
    scheme here). ``tail_cost`` seeds ``cost[n]``: what is still to be written
    once the input is covered, a terminator or nothing.

    ``tag`` comes back untouched beside the length, for the emitter to read.
    """
    inf = float("inf")
    cost: list[float] = [inf] * (n + 1)
    cost[n] = tail_cost
    # Sized up front for the forward walk to index; every entry is replaced
    # below, `options` offering an op at every position, so the seed is dead.
    choice: list[tuple[int, Op]] = [(1, None)] * n  # type: ignore[list-item]
    for i in range(n - 1, -1, -1):
        best = inf
        for length, value, tag in options(i, cost):
            if value < best:
                best, choice[i] = value, (length, tag)
        cost[i] = best
    picked = []
    at = 0
    while at < n:
        picked.append(choice[at])
        at += choice[at][0]
    return picked


class ControlBits:
    """The control byte a scheme reserves in front of the ops it describes.

    One byte per eight ops, written *before* them, so the byte is reserved when
    the group opens and each bit is set in place as its op is decided — which is
    the layout the lazily-refilling decoders read back, and why nothing has to be
    written again at the end. Call :meth:`bit` **before** writing an op's bytes:
    that is what reserves the control byte in front of them.

    Schemes disagree about which end of the byte the first bit sits at
    (``msb_first``) and about nothing else, and the disagreement is silent — read
    a stream either way round and it still decodes to something of about the
    right length, which is why it is stated rather than assumed.

    Unused bits in a short last group stay clear, which is what the known
    encoders' shift to alignment leaves behind too, and no decoder reads them
    either way: every scheme framed like this stops on a declared size or a
    terminator first.
    """

    __slots__ = ("_at", "_index", "_msb_first", "_out")

    def __init__(self, out: bytearray, *, msb_first: bool) -> None:
        self._out = out
        self._msb_first = msb_first
        self._at = -1  # where this group's control byte is reserved
        self._index = 8  # a full group, so the first bit opens a new one

    def bit(self, value: int) -> None:
        """Set the next control bit if ``value`` is true, opening a group when one
        is due — an ``int`` because a scheme selecting on one of an op's own bits
        has one in hand and nothing is gained by spelling ``bool`` around it."""
        if self._index >= 8:
            self._at = len(self._out)
            self._out.append(0)
            self._index = 0
        if value:
            self._out[self._at] |= (
                (0x80 >> self._index) if self._msb_first else 1 << self._index
            )
        self._index += 1


class FlagGroup(ControlBits):
    """:class:`ControlBits` as the LZSS family selects between its two ops.

    Those framings disagree about one further thing, just as silently: whether a
    set bit selects the back-reference or the literal (``set_means_match``).
    """

    __slots__ = ("_match_bit",)

    def __init__(
        self, out: bytearray, *, msb_first: bool, set_means_match: bool
    ) -> None:
        super().__init__(out, msb_first=msb_first)
        self._match_bit = set_means_match

    def select(self, is_match: bool) -> None:
        """Record the next op's selector, opening a group when one is due."""
        self.bit(is_match == self._match_bit)


class InterleavedWriter:
    """Bit words and the payload bytes that queue behind them, kept in step.

    The other way a scheme here frames its ops: not one control byte in front of
    eight of them (:class:`ControlBits`) but a whole word of selectors, with the
    payload bytes written *between* words. Payload queues while a word fills, the
    word goes out the instant its last bit arrives, and the queue follows it — so
    the bytes on either side of a word are the ones its bits describe, which is
    the layout a decoder that fetched the word before reading them expects.

    ``word_bits`` is the word's width and ``low_first`` which end of it the first
    bit sits at. ``eager`` says what the decoder does with a word it has not
    started: an eager one has already fetched it, so payload written with no word
    open still has to queue; a lazy one has not, so that payload can go straight
    out. Reading a stream under the wrong one lands on the wrong bytes while
    consuming exactly the right bits, which is why both are stated.

    How a part-filled last word is written out is the one thing no two of these
    schemes agree on, so ``finish`` is left to the subclass.
    """

    def __init__(self, *, word_bits: int, low_first: bool, eager: bool) -> None:
        self.out = bytearray()
        self._size = word_bits
        self._low_first = low_first
        self._eager = eager
        self._word = 0
        self._bits = 0
        self._pending = bytearray()

    def put(self, value: int, count: int) -> None:
        """Add ``count`` bits of ``value``, flushing each word as it fills."""
        if self._low_first:
            while count:
                take = min(count, self._size - self._bits)
                self._word |= (value & ((1 << take) - 1)) << self._bits
                value >>= take
                count -= take
                self._bits += take
                if self._bits == self._size:
                    self._flush()
        else:
            for shift in range(count - 1, -1, -1):
                self._word = (self._word << 1) | ((value >> shift) & 1)
                self._bits += 1
                if self._bits == self._size:
                    self._flush()

    def raw(self, chunk: bytes) -> None:
        """Payload bytes, behind the word whose bits describe them."""
        if self._bits or self._eager:
            self._pending += chunk
        else:
            self.out += chunk

    def _flush(self) -> None:
        self.out += self._word.to_bytes(self._size // 8, "little")
        self.out += self._pending
        self._pending.clear()
        self._word = 0
        self._bits = 0
