"""Relative search over a sequence of codes, for text a user typed from the
screen.

The query is cut into words at anything that is not a letter, digit or kana.
Each word of four or more letters anchors a chain; the other words follow in
order, each at its nearest fit under the bases the chain has fixed, and a word
shorter than three letters joins only once its alphabets are pinned. A chain
counts only when every word is in it and the tightest wins; what sits between
two words is kept, so the separators the user typed become table entries.
Where no chain holds every word, the best one says which words did chain.

Kana are searched in gojūon order, as :mod:`mapchar.engines.relsearch` does,
and in Shift-JIS order, with the small and voiced kana between the plain ones.
"""

from __future__ import annotations

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

    @property
    def text(self) -> str:
        return "".join(ch for _, _, ch in self.letters)

    def __len__(self) -> int:
        return len(self.letters)


def words(text: str, order: dict[str, str] = GOJUON) -> list[Word]:
    """The typed text as words, each with the text before it."""
    out: list[Word] = []
    cur: list[tuple[str, int, str]] = []
    sep = ""
    for ch in text + " ":
        for run, alphabet in order.items():
            i = alphabet.find(ch)
            if i >= 0:
                cur.append((run, i, ch))
                break
        else:
            if cur:
                out.append(Word(tuple(cur), sep))
                cur, sep = [], ""
            sep += ch
    return out


@dataclass(frozen=True)
class Chain:
    start: int
    """Position of the first matched code."""
    end: int
    """Position past the last matched code."""
    bases: dict[str, int]
    """Run name to the code of its first character."""
    placed: tuple[tuple[int, int], ...]
    """``(position, word index)`` per matched word, in position order."""

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
        :meth:`fits` succeeds."""
        if hi < lo:
            return
        letters = word.letters
        known, steps = (0, 0), (0, 0)  # (length, offset) of the longest block
        k = 0
        while k < len(letters):
            j = k
            while (
                j < len(letters)
                and letters[j][0] in bases
                and 0 <= bases[letters[j][0]] + letters[j][1] < VMAX
            ):
                j += 1
            if j - k > known[0]:
                known = (j - k, k)
            k = j + 1
        k = 0
        while k < len(letters):
            j = k + 1
            while j < len(letters) and letters[j][0] == letters[k][0]:
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
                n, k0 = known
                codes = [bases[r] + i for r, i, _ in letters[k0 : k0 + n]]
                if isinstance(values, str):
                    s, pat = values, "".join(map(chr, codes))
                elif max(codes) < 256:
                    s, pat = values, bytes(codes)
                else:  # a code no value can be
                    return
        if s is None and steps[0] >= 2:
            n, k0 = steps
            s = self.steps()
            pat = bytes(
                (b[1] - a[1]) & 255
                for a, b in zip(
                    letters[k0 : k0 + n], letters[k0 + 1 : k0 + n], strict=False
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
        seq = self.seq
        if p < 0 or p + len(word) > len(seq):
            return None
        b = dict(bases)
        for k, (r, i, _) in enumerate(word.letters):
            v = seq[p + k] - i
            if v < 0 or b.setdefault(r, v) != v:
                return None
        return b

    def next_fit(self, start: int, word: Word, bases: dict, backward=False):
        """The nearest ``(position, bases)`` from ``start`` — forward, or
        backward ending before it — where ``word`` fits, or None."""
        if backward:
            lo, hi = 0, start - len(word)
        else:
            lo, hi = start, len(self.seq) - len(word)
        for q in self.positions(word, bases, lo, hi, backward):
            nb = self.fits(q, word, bases)
            if nb is not None:
                return q, nb
        return None


def match(
    seq: list[int] | Codes, ws: list[Word], limit: int = 20, anchors: int = 6
) -> list[Chain]:
    """Chains of ``ws`` in ``seq``, found from each anchor word's fits: every
    chain holding at least half the words, up to ``limit``."""
    codes = seq if isinstance(seq, Codes) else Codes(seq)
    if not ws:
        return []
    order = [k for k, w in enumerate(ws) if len(w) >= ANCHOR] or [
        max(range(len(ws)), key=lambda k: len(ws[k]))
    ]
    order.sort(key=lambda k: -len(ws[k]))
    out: list[Chain] = []
    seen: set[int] = set()
    need = 1 if len(ws) == 1 else max(2, len(ws) // 2)
    for k0 in order[:anchors]:
        w0 = ws[k0]
        for p in codes.positions(w0, {}, 0, len(codes) - len(w0)):
            b = codes.fits(p, w0, {})
            if b is None:
                continue
            placed = [(p, k0)]
            bases, pos = b, p + len(w0)
            for k in range(k0 + 1, len(ws)):  # forward: the nearest fit
                w = ws[k]
                if len(w) < 3 and not all(r in bases for r, _, _ in w.letters):
                    continue
                r = codes.next_fit(pos, w, bases)
                if r is not None:
                    q, bases = r
                    placed.append((q, k))
                    pos = q + len(w)
            pos = p
            for k in range(k0 - 1, -1, -1):  # backward: the nearest fit
                w = ws[k]
                if len(w) < 3 and not all(r in bases for r, _, _ in w.letters):
                    continue
                r = codes.next_fit(pos, w, bases, backward=True)
                if r is not None:
                    q, bases = r
                    placed.append((q, k))
                    pos = q
            placed.sort()
            if placed[0][0] in seen or len(placed) < need:
                continue
            seen.add(placed[0][0])
            last = placed[-1]
            out.append(
                Chain(placed[0][0], last[0] + len(ws[last[1]]), bases, tuple(placed))
            )
            if len(out) >= limit:
                return out
    return out


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
    the codes found there."""
    out = []
    for (p, k), (q, k2) in zip(c.placed, c.placed[1:], strict=False):
        if k2 == k + 1:
            out.append((ws[k2].sep, list(seq[p + len(ws[k]) : q])))
    return out


def letters_at(c: Chain, ws: list[Word]) -> list[tuple[int, str]]:
    """``(position, character)`` of every matched letter."""
    out = []
    for pos, k in c.placed:
        for off, (_, _, ch) in enumerate(ws[k].letters):
            out.append((pos + off, ch))
    return out


def decoder(
    seq: list[int], c: Chain, ws: list[Word], order: dict[str, str]
) -> dict[int, str]:
    """Code to character from a chain: the letters it matched, whole alphabets
    as relative search lays them out, and separators of the typed length."""
    dec: dict[int, str] = {}
    for pos, ch in letters_at(c, ws):
        dec[seq[pos]] = ch
    for run, base in c.bases.items():
        for i, ch in enumerate(order[run]):
            dec.setdefault(base + i, ch)
    for sep, gap in gaps(seq, c, ws):
        if len(sep) == len(gap):
            for ch, code in zip(sep, gap, strict=True):
                dec.setdefault(code, ch)
    return dec


def changes(keys: list) -> list[int]:
    """Positions whose key differs from the one before, and the first."""
    return list(compress(range(len(keys)), _chain((True,), map(ne, keys[1:], keys))))
