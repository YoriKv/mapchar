"""Relative search over a sequence of codes, for text a user typed from the
screen.

The query is cut into words at anything that is not a letter, digit or kana.
The six longest words of four or more letters each anchor chains (the longest
word does when none has four); the other words follow in order, each at its
nearest fit under the bases the chain has fixed and at most :data:`MAX_GAP`
codes from the word before (:data:`WIDE_GAP` when that leaves every chain a
word short), and a word shorter than three letters joins only once its
alphabets are pinned. A chain counts only when every word is in it,
and the tightest wins; what sits between two words is kept, so the separators
the user typed become table entries. Where no chain holds every word, the best
one says which words did chain. A sequence can be searched backwards too, for
text stored reversed.

Kana are searched in gojūon order, as :mod:`mapchar.engines.relsearch` does,
and in Shift-JIS order, with the small and voiced kana between the plain ones.
In gojūon order a voiced kana is its plain kana and a mark code, after it or
before it, the same code for every kana the mark voices.
"""

from __future__ import annotations

import heapq
import unicodedata
from collections import Counter
from dataclasses import dataclass
from itertools import chain as _chain
from itertools import compress, repeat
from operator import and_, ne, sub

from mapchar.engines.relsearch import HIRAGANA, KATAKANA, RUNS

GOJUON: dict[str, str] = dict(RUNS)
"""Run name to its characters in code order, kana in gojūon order."""

JIS: dict[str, str] = {
    **{k: v for k, v in RUNS.items() if k not in (HIRAGANA, KATAKANA)},
    HIRAGANA: "".join(chr(c) for c in range(0x3041, 0x3094)),
    KATAKANA: "".join(chr(c) for c in range(0x30A1, 0x30F5)),
}
"""The same runs with kana in Shift-JIS (and Unicode) order."""

VMAX = 0x10FFFF
"""A value at or above it is kept as one character; no code searched for is."""

ANCHOR = 4
"""Letters a word needs to anchor a chain."""

MAX_GAP = 40
"""The most codes between two neighbouring words of a chain."""

WIDE_GAP = 128
"""The most codes between two words when no chain holds every word within
:data:`MAX_GAP`: the rows between two lines of a tilemap drawn two rows a
line, or a row of 64 cells."""

MARKS = "゙゚"
"""The combining voiced and semi-voiced sound marks."""

MARK = "mark:"
"""A chain's :attr:`Chain.bases` key for a mark's code: ``mark:`` and the
mark."""

BEFORE = "mark:before"
"""A chain's :attr:`Chain.bases` key saying the mark code comes before its
kana (1) or after it (0)."""


def has_kana(text: str) -> bool:
    return any(0x3040 <= ord(c) < 0x3100 for c in text)


def orders_for(text: str) -> list[dict[str, str]]:
    """The orders worth searching ``text`` in: gojūon, and Shift-JIS when it
    holds kana."""
    return [GOJUON, JIS] if has_kana(text) else [GOJUON]


@dataclass(frozen=True)
class Word:
    letters: tuple[tuple[str, int, str], ...]
    """``(run, index in the run, character)`` per letter."""
    sep: str
    """What the user typed before it: a space, punctuation."""
    marks: tuple[str, ...] = ()
    """Per letter, the mark voicing it as a code of its own, or ``""``; empty
    when no letter has one."""

    @property
    def text(self) -> str:
        marks = self.marks or ("",) * len(self.letters)
        return unicodedata.normalize(
            "NFC",
            "".join(
                ch + mk for (_, _, ch), mk in zip(self.letters, marks, strict=True)
            ),
        )

    def __len__(self) -> int:
        return len(self.letters)

    @property
    def span(self) -> int:
        """The codes it takes: a letter each, and each mark one more."""
        return len(self.letters) + sum(1 for m in self.marks if m)

    def offsets(self, before: int = 0) -> list[int]:
        """Each letter's code position from the word's first code, its mark
        before it (``before``) or after it."""
        out, pos = [], 0
        marks = self.marks or ("",) * len(self.letters)
        for mk in marks:
            if mk and before:
                pos += 1
            out.append(pos)
            pos += 2 if mk and not before else 1
        return out


def _letter(ch: str, order: dict[str, str]) -> tuple[str, int, str] | None:
    for run, alphabet in order.items():
        i = alphabet.find(ch)
        if i >= 0:
            return run, i, ch
    return None


def words(text: str, order: dict[str, str] = GOJUON) -> list[Word]:
    """The typed text as words, each with the text before it. A voiced kana
    no run of ``order`` holds is its plain kana with the mark as a code of
    its own."""
    out: list[Word] = []
    cur: list[tuple[str, int, str]] = []
    marks: list[str] = []
    sep = ""
    for ch in unicodedata.normalize("NFC", text) + " ":
        hit, mark = _letter(ch, order), ""
        if hit is None and ch in MARKS and cur and not marks[-1]:
            marks[-1] = ch  # a mark that composes with nothing, typed apart
            continue
        if hit is None:
            d = unicodedata.normalize("NFD", ch)
            if len(d) == 2 and d[1] in MARKS:
                hit, mark = _letter(d[0], order), d[1]
        if hit is not None:
            cur.append(hit)
            marks.append(mark)
            continue
        if cur:
            out.append(Word(tuple(cur), sep, tuple(marks) if any(marks) else ()))
            cur, marks, sep = [], [], ""
        sep += ch
    return out


@dataclass(frozen=True)
class Chain:
    start: int
    """Position of the first matched code."""
    end: int
    """Position past the last matched code."""
    bases: dict[str, int]
    """Run name to the code of its first character; for kana voiced by a
    mark code, :data:`MARK` and the mark to its code, and :data:`BEFORE`."""
    placed: tuple[tuple[int, int], ...]
    """``(position, word index)`` per matched word, in word order: the
    position of the word's first code."""
    reverse: bool = False
    """The text runs backwards through the sequence: each word from its
    position down."""

    @property
    def span(self) -> int:
        return self.end - self.start

    @property
    def count(self) -> int:
        return len(self.placed)


class Codes:
    """A sequence searched for words, with the byte strings that let
    :meth:`str.find` pick the positions to test: its values when a word's codes
    are known, its steps (neighbour differences modulo 256) when only their
    differences are. Both narrow the positions tested, never decide a match."""

    def __init__(self, seq: list[int]):
        self.seq = seq
        self._values: bytes | str | bool | None = None
        self._steps: bytes | None = None

    def __len__(self) -> int:
        return len(self.seq)

    def values(self) -> bytes | str | bool:
        if self._values is None:
            seq = self.seq
            lo, hi = (min(seq), max(seq)) if seq else (0, 0)
            if lo < 0:
                self._values = False
            elif hi < 256:
                self._values = bytes(seq)
            elif hi < VMAX:
                self._values = "".join(map(chr, seq))
            else:
                self._values = "".join(map(chr, map(min, seq, repeat(VMAX))))
        return self._values

    def steps(self) -> bytes:
        if self._steps is None:
            seq = self.seq
            self._steps = bytes(map(and_, map(sub, seq[1:], seq), repeat(255)))
        return self._steps

    def positions(self, word: Word, bases: dict, lo: int, hi: int, backward=False):
        """Every position in ``lo..hi`` where ``word`` could fit under
        ``bases``, in the order they are tried: a superset of where
        :meth:`fits` succeeds. Only letters with no mark code narrow it, since
        those alone sit at the same code positions whichever side the marks
        are on."""
        if hi < lo:
            return
        letters = word.letters
        plain = [not m for m in (word.marks or ("",) * len(letters))]
        offs = word.offsets()
        known, steps = (0, 0), (0, 0)  # (length, letter) of the longest block
        k = 0
        while k < len(letters):
            j = k
            while (
                j < len(letters)
                and plain[j]
                and letters[j][0] in bases
                and 0 <= bases[letters[j][0]] + letters[j][1] < VMAX
            ):
                j += 1
            if j - k > known[0]:
                known = (j - k, k)
            k = j + 1
        k = 0
        while k < len(letters):
            if not plain[k]:
                k += 1
                continue
            j = k + 1
            while j < len(letters) and plain[j] and letters[j][0] == letters[k][0]:
                j += 1
            if j - k > steps[0]:
                steps = (j - k, k)
            k = j
        s: bytes | str | None = None
        k0 = 0
        pat: bytes | str = b""
        if known[0] and (steps[0] < 2 or known[0] >= steps[0] - 1):
            values = self.values()
            if values is not False:
                n, kl = known
                k0 = offs[kl]
                codes = [bases[r] + i for r, i, _ in letters[kl : kl + n]]
                if isinstance(values, str):
                    s, pat = values, "".join(map(chr, codes))
                elif max(codes) < 256:
                    s, pat = values, bytes(codes)
                else:  # a code no value can be
                    return
        if s is None and steps[0] >= 2:
            n, kl = steps
            k0 = offs[kl]
            s = self.steps()
            pat = bytes(
                (b[1] - a[1]) & 255
                for a, b in zip(
                    letters[kl : kl + n], letters[kl + 1 : kl + n], strict=False
                )
            )
        if s is None:
            yield from (range(hi, lo - 1, -1) if backward else range(lo, hi + 1))
            return
        if not backward:
            j = s.find(pat, lo + k0)
            while j >= 0 and j - k0 <= hi:
                yield j - k0
                j = s.find(pat, j + 1)
        else:
            j = s.rfind(pat, lo + k0, hi + k0 + len(pat))
            while j >= 0:
                yield j - k0
                j = s.rfind(pat, lo + k0, j - 1 + len(pat))

    def fits(self, p: int, word: Word, bases: dict) -> dict | None:
        """The bases after matching ``word`` at ``p``, or None."""
        if word.marks:
            for before in (bases[BEFORE],) if BEFORE in bases else (0, 1):
                b = self._fits_marked(p, word, bases, before)
                if b is not None:
                    b[BEFORE] = before
                    return b
            return None
        seq = self.seq
        if p < 0 or p + len(word) > len(seq):
            return None
        b = dict(bases)
        for k, (r, i, _) in enumerate(word.letters):
            v = seq[p + k] - i
            if v < 0 or b.setdefault(r, v) != v:
                return None
        return b

    def _fits_marked(self, p: int, word: Word, bases: dict, before: int):
        seq = self.seq
        if p < 0 or p + word.span > len(seq):
            return None
        b = dict(bases)
        q = p
        for (r, i, _), mk in zip(word.letters, word.marks, strict=True):
            if mk and before:
                if b.setdefault(MARK + mk, seq[q]) != seq[q]:
                    return None
                q += 1
            v = seq[q] - i
            if v < 0 or b.setdefault(r, v) != v:
                return None
            q += 1
            if mk and not before:
                if b.setdefault(MARK + mk, seq[q]) != seq[q]:
                    return None
                q += 1
        return b

    def next_fit(
        self,
        start: int,
        word: Word,
        bases: dict,
        backward=False,
        reach: int | None = None,
    ):
        """The nearest ``(position, bases)`` from ``start`` — forward, or
        backward ending before it — where ``word`` fits, at most ``reach``
        codes away, or None."""
        if backward:
            lo, hi = 0, start - word.span
            if reach is not None:
                lo = max(lo, hi - reach)
        else:
            lo, hi = start, len(self.seq) - word.span
            if reach is not None:
                hi = min(hi, start + reach)
        for q in self.positions(word, bases, lo, hi, backward):
            nb = self.fits(q, word, bases)
            if nb is not None:
                return q, nb
        return None


def _flip(c: Chain, n: int) -> Chain:
    """A chain found in the reversed sequence of ``n`` codes, in the
    sequence's own positions, or back."""
    return Chain(
        n - c.end,
        n - c.start,
        c.bases,
        tuple((n - 1 - p, k) for p, k in c.placed),
        not c.reverse,
    )


def match(
    seq: list[int] | Codes,
    ws: list[Word],
    limit: int = 20,
    anchors: int = 6,
    reverse: bool = False,
) -> list[Chain]:
    """Chains of ``ws`` in ``seq``, built from every fit of each anchor word:
    the ``limit`` best of those holding at least half the words — the most
    words, then the tightest, then the first. Words follow at most
    :data:`MAX_GAP` codes apart, or :data:`WIDE_GAP` when that leaves every
    chain short of a word. ``reverse`` searches the sequence backwards."""
    codes = seq if isinstance(seq, Codes) else Codes(seq)
    if reverse:
        n = len(codes)
        found = match(Codes(codes.seq[::-1]), ws, limit, anchors)
        return [_flip(c, n) for c in found]
    if not ws:
        return []
    found = _match(codes, ws, limit, anchors, MAX_GAP)
    if found and found[0].count < len(ws):
        wider = _match(codes, ws, limit, anchors, WIDE_GAP)
        if wider and wider[0].count > found[0].count:
            return wider
    return found


def _match(codes: Codes, ws: list[Word], limit: int, anchors: int, reach: int):
    order = [k for k, w in enumerate(ws) if len(w) >= ANCHOR] or [
        max(range(len(ws)), key=lambda k: len(ws[k]))
    ]
    order.sort(key=lambda k: -len(ws[k]))
    best: list[tuple] = []  # the worst kept first: (count, -span, -start, chain)
    seen: set[int] = set()
    need = 1 if len(ws) == 1 else max(2, len(ws) // 2)
    for k0 in order[:anchors]:
        w0 = ws[k0]
        for p in codes.positions(w0, {}, 0, len(codes) - w0.span):
            b = codes.fits(p, w0, {})
            if b is None:
                continue
            placed = [(p, k0)]
            bases, pos = b, p + w0.span
            for k in range(k0 + 1, len(ws)):  # forward: the nearest fit
                w = ws[k]
                if len(w) < 3 and not all(r in bases for r, _, _ in w.letters):
                    continue
                r = codes.next_fit(pos, w, bases, reach=reach)
                if r is not None:
                    q, bases = r
                    placed.append((q, k))
                    pos = q + w.span
            pos = p
            for k in range(k0 - 1, -1, -1):  # backward: the nearest fit
                w = ws[k]
                if len(w) < 3 and not all(r in bases for r, _, _ in w.letters):
                    continue
                r = codes.next_fit(pos, w, bases, backward=True, reach=reach)
                if r is not None:
                    q, bases = r
                    placed.append((q, k))
                    pos = q
            placed.sort()
            if placed[0][0] in seen or len(placed) < need:
                continue
            seen.add(placed[0][0])
            last = placed[-1]
            c = Chain(placed[0][0], last[0] + ws[last[1]].span, bases, tuple(placed))
            item = (c.count, -c.span, -c.start, c)
            if len(best) < limit:
                heapq.heappush(best, item)
            elif item[:3] > best[0][:3]:
                heapq.heapreplace(best, item)
    return [c for *_, c in sorted(best, key=lambda x: x[:3], reverse=True)]


def complete(found: list[Chain], ws: list[Word]) -> list[Chain]:
    """The chains holding every word, tightest first."""
    return sorted((c for c in found if c.count == len(ws)), key=lambda c: c.span)


def chains(
    seq: list[int] | Codes, text: str, order: dict[str, str] = GOJUON, limit=20
) -> tuple[list[Chain], list[Word]]:
    """The complete chains of ``text`` in ``seq``, tightest first, and its
    words."""
    ws = words(text, order)
    return complete(match(seq, ws, limit), ws), ws


def gaps(seq: list[int], c: Chain, ws: list[Word]) -> list[tuple[str, list[int]]]:
    """What sat between neighbouring matched words: the typed separator and
    the codes found there, in the text's order."""
    if c.reverse:
        return gaps(seq[::-1], _flip(c, len(seq)), ws)
    out = []
    for (p, k), (q, k2) in zip(c.placed, c.placed[1:], strict=False):
        if k2 == k + 1:
            out.append((ws[k2].sep, list(seq[p + ws[k].span : q])))
    return out


def letters_at(c: Chain, ws: list[Word]) -> list[tuple[int, str]]:
    """``(position, character)`` of every matched letter; a voiced kana's is
    its plain kana's, at its plain kana's code."""
    out = []
    before = c.bases.get(BEFORE, 0)
    for pos, k in c.placed:
        for off, (_, _, ch) in zip(ws[k].offsets(before), ws[k].letters, strict=True):
            out.append((pos - off if c.reverse else pos + off, ch))
    return out


def decoder(
    seq: list[int], c: Chain, ws: list[Word], order: dict[str, str]
) -> dict[int, str]:
    """Code to character from a chain: the letters it matched, a mark code as
    its combining mark, whole alphabets as relative search lays them out, and
    separators of the typed length. A separator standing on several codes is
    the one most often under it; the others — a line break where a space was
    typed — are left out."""
    if c.reverse:
        return decoder(seq[::-1], _flip(c, len(seq)), ws, order)
    dec: dict[int, str] = {}
    for pos, ch in letters_at(c, ws):
        dec[seq[pos]] = ch
    for key, code in c.bases.items():
        if key.startswith(MARK) and key != BEFORE:
            dec.setdefault(code, key[len(MARK) :])
    for run, base in c.bases.items():
        if run in order:
            for i, ch in enumerate(order[run]):
                dec.setdefault(base + i, ch)
    votes: dict[str, Counter] = {}
    for sep, gap in gaps(seq, c, ws):
        if len(sep) == len(gap):
            for ch, code in zip(sep, gap, strict=True):
                votes.setdefault(ch, Counter())[code] += 1
    for ch, counts in votes.items():
        dec.setdefault(counts.most_common(1)[0][0], ch)
    return dec


def changes(keys: list) -> list[int]:
    """Positions whose key differs from the one before, and the first."""
    return list(compress(range(len(keys)), _chain((True,), map(ne, keys[1:], keys))))
