"""Relative search: find text without a table.

A query such as ``Hello`` becomes constraints on the differences between
codes. Letters of one case form a run whose codes are consecutive, digits
another; ``?`` matches anything. A hit reports the base code of every run
it pinned down, which is what seeds a table.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable
from dataclasses import dataclass

from mapchar.core.bits import bytes_to_bits
from mapchar.core.table import Entry, EntryKind
from mapchar.core.tokens import escape_text

UPPER = "upper"
LOWER = "lower"
DIGIT = "digit"
RUNS = {
    UPPER: "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    LOWER: "abcdefghijklmnopqrstuvwxyz",
    DIGIT: "0123456789",
}


@dataclass(frozen=True)
class Hit:
    offset: int
    """Byte offset of the first code."""
    width: int
    """Bytes per code."""
    endian: str
    bases: dict[str, int]
    """Run name to the code of its first character (``A``, ``a`` or ``0``)."""
    codes: tuple[int, ...]

    @property
    def length(self) -> int:
        return len(self.codes) * self.width


@dataclass(frozen=True)
class _Term:
    run: str | None
    index: int


def _terms(query: str) -> list[_Term]:
    terms: list[_Term] = []
    for ch in query:
        if ch == "?":
            terms.append(_Term(None, 0))
            continue
        for run, alphabet in RUNS.items():
            i = alphabet.find(ch)
            if i >= 0:
                terms.append(_Term(run, i))
                break
        else:
            raise ValueError(f"{ch!r} is not a letter, digit or '?'")
    return terms


def _codes(data: bytes, width: int, endian: str, phase: int) -> array:
    if width == 1:
        return array("B", data)
    body = data[phase:]
    body = body[: len(body) - len(body) % 2]
    codes = array("H", body)
    import sys

    if (sys.byteorder == "little") != (endian == "little"):
        codes.byteswap()
    return codes


def relative_search(
    data: bytes,
    query: str,
    *,
    widths: tuple[int, ...] = (1, 2),
    endians: tuple[str, ...] = ("little", "big"),
    case_gap: bool = True,
    limit: int = 500,
    progress: Callable[[int, int], bool] | None = None,
) -> list[Hit]:
    """Every place the query's relative pattern occurs.

    With ``case_gap`` off, lower case must follow upper case directly
    (``a == A + 26``). ``progress(done, total)`` returns False to cancel.
    """
    terms = _terms(query)
    if not any(t.run for t in terms):
        raise ValueError("the query needs at least one letter or digit")
    n = len(terms)
    hits: list[Hit] = []
    combos = [(w, e) for w in widths for e in (endians if w > 1 else ("little",))]
    total = len(combos) * len(data)
    done = 0
    for width, endian in combos:
        for phase in range(width):
            codes = _codes(data, width, endian, phase)
            count = len(codes)
            for i in range(count - n + 1):
                if progress is not None and i % 65536 == 0:
                    if not progress(done + i * width, total):
                        return hits
                bases: dict[str, int] = {}
                ok = True
                for k, term in enumerate(terms):
                    if term.run is None:
                        continue
                    value = codes[i + k]
                    base = bases.get(term.run)
                    if base is None:
                        base = value - term.index
                        if base < 0:
                            ok = False
                            break
                        bases[term.run] = base
                    elif base != value - term.index:
                        ok = False
                        break
                if ok and not case_gap and UPPER in bases and LOWER in bases:
                    ok = bases[LOWER] == bases[UPPER] + 26
                if ok:
                    hits.append(
                        Hit(
                            i * width + phase,
                            width,
                            endian,
                            dict(bases),
                            tuple(codes[i : i + n]),
                        )
                    )
                    if len(hits) >= limit:
                        return hits
        done += len(data)
    return hits


def _code_bits(code: int, bit_width: int, endian: str) -> str:
    if bit_width % 8 == 0:
        order = "big" if endian == "big" else "little"
        return bytes_to_bits(code.to_bytes(bit_width // 8, order))
    return format(code, f"0{bit_width}b")


def entries_from_base(
    base: int, bit_width: int, endian: str, chars: str
) -> list[Entry]:
    """TEXT entries for ``chars`` over consecutive codes from ``base``.

    The first code that does not fit ``bit_width`` bits ends the run.
    """
    entries: list[Entry] = []
    for i, ch in enumerate(chars):
        code = base + i
        if code >= 1 << bit_width:
            break
        entries.append(
            Entry(_code_bits(code, bit_width, endian), EntryKind.TEXT, escape_text(ch))
        )
    return entries


def entries_from_hit(
    hit: Hit, runs: tuple[str, ...] = (UPPER, LOWER, DIGIT)
) -> list[Entry]:
    """Table entries for every run the hit pinned down, as full alphabets."""
    entries: list[Entry] = []
    for run in runs:
        base = hit.bases.get(run)
        if base is None:
            continue
        entries.extend(entries_from_base(base, hit.width * 8, hit.endian, RUNS[run]))
    return entries
