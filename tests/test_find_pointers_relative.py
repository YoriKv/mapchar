"""Find Pointers over the relative mappings
(:func:`mapchar.engines.pointers.discover`): no address counts as a pointer
to the string it sits in, each pointer keeps its own value, a relative
reading ranks after the others on a full tie, the scan answers exactly what
reading every address one at a time would — fill included — a mapping that
raises loses only its own reading, and a Stop is heard inside a long step.
Fill itself is :mod:`test_find_pointers_fill`'s."""

from __future__ import annotations

import random

from helpers import ASCII_TABLE, table_set
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    RangeSource,
    WriteMode,
)
from mapchar.core.mapping import pointer_bytes, pointer_offset, read_pointer
from mapchar.engines import pointers as engine
from mapchar.engines.pointers import _relative_candidates, discover
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import (
    apply_splices,
    layout_block,
    record_spans,
    record_start,
)
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import default_registry, resolve_mapping

REG = default_registry()
ALL = {mid: resolve_mapping(REG, mid) for mid in REG.ids(Stage.MAPPING)}
RELATIVE = {mid: resolve_mapping(REG, mid) for mid in ("relative", "relative-signed")}
WITH_LINEAR = {**RELATIVE, "linear": resolve_mapping(REG, "linear")}
ASCII = table_set(ASCII_TABLE, "main")


def _spans_of(ex, cfg) -> dict[int, list[tuple[int, int]]]:
    """What Find Pointers passes: each record's bytes, by where it starts."""
    return {record_start(r, cfg): record_spans(r, cfg) for r in ex.strings}


def _blank_rom() -> tuple[bytes, list[int], dict[int, list[tuple[int, int]]]]:
    """``AB,C,AB,C`` at $40 and six empty strings after them, with a table of
    big-endian relative pointers at $00 to the first four: the strings, and
    the bytes of each."""
    rom = bytearray(b"\xff" * 0x60)
    rom[0x40:0x4A] = bytes.fromhex("41 42 00 43 00 41 42 00 43 00")
    rom[0x4A:0x50] = bytes(6)
    for i, string in enumerate((0x40, 0x43, 0x45, 0x48)):
        rom[2 * i : 2 * i + 2] = pointer_bytes(string - 2 * i, 2, "big")
    starts = [0x40, 0x43, 0x45, 0x48, *range(0x4A, 0x50)]
    ends = dict(zip(starts, [0x43, 0x45, 0x48, 0x4A, *range(0x4B, 0x51)], strict=True))
    return bytes(rom), starts, {s: [(s, e)] for s, e in ends.items()}


def _inside(at: int, size: int, pieces) -> bool:
    return any(at < hi and at + size > lo for lo, hi in pieces)


def _inside_own_string(cands, spans) -> list:
    spans = spans or {}
    return [
        (c.mapping_id, c.endian, start, a)
        for c in cands
        for start, found in c.hits.items()
        for a in found
        if _inside(a, c.size, spans.get(start) or [(start, start + 1)])
    ]


def test_blank_strings_are_not_pointers_to_themselves():
    rom, starts, spans = _blank_rom()
    for given in (spans, None):
        cands = discover(rom, starts, WITH_LINEAR, sizes=(2,), spans=given)
        best = cands[0]
        assert best.mapping_id.startswith("relative")
        assert (best.size, best.endian, best.run_explained()) == (2, "big", 4)
        assert (best.source().start, best.source().stop) == (0, 8)
        refs = best.refs()
        assert [refs[s][0].address for s in (0x40, 0x43, 0x45, 0x48)] == [0, 2, 4, 6]
        assert _inside_own_string(cands, given) == []


def test_the_self_pointers_were_what_won():
    """Without the rule, the empty strings' zeros join the table."""
    rom, starts, spans = _blank_rom()
    keep = engine._overlaps
    engine._overlaps = lambda *args: False
    try:
        best = discover(rom, starts, WITH_LINEAR, sizes=(2,), spans=spans)[0]
    finally:
        engine._overlaps = keep
    assert _inside_own_string([best], spans)


def test_an_empty_last_string_before_fill_takes_no_stray_pointer():
    """A signed table at $00 to seven strings; the empty one after them has
    $FFFF fill right behind it, which reads as -1. Attached and written
    packed, the table's pointers follow the text and nothing lands on it."""
    rom = bytearray(b"\xff" * 0x80)
    at, strings = 0x40, []
    for text in ("ONE", "TWO", "SIX", "TEN", "FOUR", "FIVE", "GO"):
        strings.append(at)
        rom[at : at + len(text) + 1] = text.encode() + b"\x00"
        at += len(text) + 1
    rom[at] = 0
    assert (strings[-1], at) == (0x5A, 0x5D)
    for i, string in enumerate(strings):
        rom[2 * i : 2 * i + 2] = pointer_bytes(string - 2 * i, 2, "big")
    cfg = BlockConfig(
        RangeSource(0x40, 0x70), EndToken(), "main", write_mode=WriteMode.PACKED
    )
    ex = extract(bytes(rom), cfg, ASCII, REG)
    assert ex.strings[7].start == 0x5D and ex.strings[7].original_text() == "[end]"
    cands = discover(
        bytes(rom), [r.start for r in ex.strings], ALL, spans=_spans_of(ex, cfg)
    )
    best = cands[0]
    assert best.relative and (best.size, best.endian, best.explained) == (2, "big", 7)
    refs = best.refs()
    assert sorted(refs) == strings
    assert [ref.address for start in strings for ref in refs[start]] == list(
        range(0, 14, 2)
    )
    for rec in ex.strings:
        rec.pointers = refs.get(rec.start, ())
    ex.strings[0].replacement = "HELLO WORLD[end]"
    res = layout_block(bytes(rom), cfg, ASCII, ex.strings, REG)
    assert res.ok, res.problems
    out = apply_splices(bytes(rom), res.splices)
    table = BlockConfig(best.source(), EndToken(), "main")
    assert [r.original_text() for r in extract(out, table, ASCII, REG).strings] == [
        "HELLO WORLD[end]",
        "TWO[end]",
        "SIX[end]",
        "TEN[end]",
        "FOUR[end]",
        "FIVE[end]",
        "GO[end]",
    ]
    text = b"HELLO WORLD\x00TWO\x00SIX\x00TEN\x00FOUR\x00FIVE\x00GO\x00\x00"
    assert out[0x40 : 0x40 + len(text)] == text


def test_a_relative_reading_ranks_after_a_value_on_a_full_tie():
    """A pointer at $00 holds the same value read either way."""
    rom = bytearray(b"\xff" * 0x20)
    rom[0x10:0x13] = b"AB\x00"
    rom[0x00:0x02] = pointer_bytes(0x10, 2, "little")
    cands = discover(bytes(rom), [0x10], WITH_LINEAR, sizes=(2,), endians=("little",))
    assert [c.mapping_id for c in cands] == ["linear", "relative", "relative-signed"]


def test_one_byte_relative_pointers_are_found():
    rom = bytearray(b"\xff" * 0x30)
    rom[0x20:0x23] = b"AB\x00"
    rom[0x10] = 0x20 - 0x10
    rom[0x11] = 0x20 - 0x11
    cands = discover(bytes(rom), [0x20], RELATIVE, sizes=(1,))
    assert {(c.mapping_id, c.endian): c.hits for c in cands} == {
        (mid, endian): {0x20: [0x10, 0x11]}
        for mid in ("relative", "relative-signed")
        for endian in ("little", "big")
    }


def test_a_pointer_in_a_skip_range_inside_its_string_is_found():
    """The string at $26 is read across the skip from $2C to $30, which is
    where its pointer sits: not the string's bytes."""
    rom = bytearray(b"\xff" * 0x40)
    rom[0x20:0x26] = b"ABCAB\x00"
    rom[0x26:0x2C] = b"ABCABC"
    rom[0x2C:0x2E] = pointer_bytes(0x26, 2, "little")
    rom[0x30:0x33] = b"AB\x00"
    cfg = BlockConfig(
        RangeSource(0x20, 0x33), EndToken(), "main", skips=((0x2C, 0x30),)
    )
    ex = extract(bytes(rom), cfg, ASCII, REG)
    assert [(r.start, r.end) for r in ex.strings] == [(0x20, 0x26), (0x26, 0x33)]
    linear = {"linear": resolve_mapping(REG, "linear")}
    cand = discover(bytes(rom), [0x26], linear, sizes=(2,), spans=_spans_of(ex, cfg))
    assert cand[0].hits == {0x26: [0x2C]}


def test_each_pointer_to_one_string_keeps_its_own_value():
    rom = bytearray(b"\xff" * 0x30)
    rom[0x10:0x13] = bytes.fromhex("41 42 00")
    rom[0x00:0x02] = pointer_bytes(0x10 - 0x00, 2, "big")
    rom[0x20:0x22] = pointer_bytes(0x10 - 0x20, 2, "big")
    spans = {0x10: [(0x10, 0x13)]}
    cands = discover(bytes(rom), [0x10], RELATIVE, sizes=(2,), spans=spans)
    cand = next(
        c for c in cands if (c.mapping_id, c.endian) == ("relative-signed", "big")
    )
    refs = cand.refs()[0x10]
    assert [(r.address, r.value) for r in refs] == [(0x00, 0x0010), (0x20, 0xFFF0)]


def _random_case(rng: random.Random):
    """Random bytes with stretches of fill — a pattern of one to eight bytes
    repeated, cut off at any length — some strings, one of them read across
    a skip, and relative pointers to them of every width, order and sign
    planted."""
    data = bytearray(rng.randbytes(rng.randrange(200, 2500)))
    for _ in range(rng.randrange(6)):
        at = rng.randrange(len(data))
        n = min(rng.randrange(4, 90), len(data) - at)
        pattern = rng.choice((b"\x00", b"\xff", rng.randbytes(rng.randrange(1, 9))))
        data[at : at + n] = (pattern * 90)[:n]
    starts = sorted(set(rng.sample(range(len(data)), rng.randrange(1, 7))))
    spans = {}
    for s in starts:
        end = s + rng.randrange(0, 12)
        spans[s] = [(s, end)] if rng.random() < 0.8 else [(s, s + 2), (s + 6, end)]
    for _ in range(rng.randrange(12)):
        size, endian = rng.choice((1, 2, 3, 4)), rng.choice(("little", "big"))
        at = rng.randrange(len(data) - size)
        target = rng.choice(starts) - rng.choice((0, 1, 3))
        data[at : at + size] = pointer_bytes(target - at, size, endian)
    offsets = tuple(sorted(rng.sample(range(5), rng.randrange(1, 4))))
    return bytes(data), starts, spans, offsets


def _fill_stretches(data: bytes) -> dict[int, list[tuple[int, int]]]:
    """Every stretch of ``data`` that repeats a period of one to eight bytes
    at least four times and over at least eight bytes, by period, worked out
    over the whole of it: the reference the search's local reading is held
    to."""
    out: dict[int, list[tuple[int, int]]] = {}
    for period in range(1, 9):
        need = max(8, 4 * period)
        runs, run = [], None
        for i in range(len(data) - period + 1):
            if i < len(data) - period and data[i] == data[i + period]:
                run = i if run is None else run
            elif run is not None:
                if i + period - run >= need:
                    runs.append((run, i + period))
                run = None
        out[period] = runs
    return out


def _string_at(data, mapping, size, endian, targets, at):
    """The string a ``size``-byte pointer at ``at`` reaches, read one value
    at a time, or None."""
    if not 0 <= at <= len(data) - size:
        return None
    value = read_pointer(data, at, size, endian) or 0
    reach = pointer_offset(mapping, value, size, 0, at)
    return targets.get(reach) if reach is not None else None


def _one_at_a_time(data, starts, spans, mappings, offsets, sizes=(2, 3, 4)):
    """What reading every address, one at a time, through each mapping finds:
    ``(mapping, size, endian, offset)`` to ``{start: [(address, value)]}``.
    No pointer sits in its own string's bytes, and a relative one inside a
    stretch of fill whose word one period away reaches nothing is fill."""
    stretches = _fill_stretches(data)
    out = {}
    for mid, mapping in mappings.items():
        relative = getattr(mapping, "relative", False)
        for size in sizes:
            for endian in ("little", "big"):
                for off in offsets:
                    targets = {s - off: s for s in starts if s >= off}
                    reading = (data, mapping, size, endian, targets)

                    def fill(at, size=size, reading=reading):
                        return any(
                            lo <= at
                            and at + size <= hi
                            and lo <= n
                            and n + size <= hi
                            and _string_at(*reading, n) is None
                            for period, runs in stretches.items()
                            for lo, hi in runs
                            for n in (at - period, at + period)
                        )

                    hits: dict[int, list] = {}
                    for at in range(len(data) - size + 1):
                        start = _string_at(*reading, at)
                        if start is None:
                            continue
                        if _inside(at, size, spans.get(start) or [(start, start + 1)]):
                            continue
                        if relative and fill(at):
                            continue
                        value = read_pointer(data, at, size, endian)
                        hits.setdefault(start, []).append((at, value))
                    if hits:
                        out[mid, size, endian, off] = hits
    return out


def test_discovery_finds_what_reading_every_address_finds():
    rng = random.Random(1234)
    for _ in range(80):
        data, starts, spans, offsets = _random_case(rng)
        sizes = rng.choice(((2, 3, 4), (1, 2)))
        cands = discover(
            data, starts, WITH_LINEAR, sizes=sizes, offsets=offsets, spans=spans
        )
        got = {
            (c.mapping_id, c.size, c.endian, c.offset): {
                start: [(a, c.address_values.get(a, c.values[start])) for a in found]
                for start, found in c.hits.items()
            }
            for c in cands
        }
        want = _one_at_a_time(data, starts, spans, WITH_LINEAR, offsets, sizes)
        assert got == want


def test_both_ways_of_scanning_find_every_address_that_reaches():
    """Long enough for many pages of the top search, with fill in it for the
    pages read a word at a time instead."""
    rng = random.Random(99)
    data = bytearray(rng.randbytes(0x30000))
    data[0x8000:0x9800] = bytes(0x1800)
    data[0x21000:0x22000] = b"\xff" * 0x1000
    targets = {0x8100, 0x20FF0, 0x2FFF0, 0x123}
    for at, target in ((0x10, 0x20FF0), (0x2FF00, 0x123), (0x8100, 0x8100)):
        data[at : at + 4] = pointer_bytes(target - at, 4, "big")
    data = bytes(data)
    for size in (2, 3, 4):
        full = 1 << (size * 8)
        for endian in ("little", "big"):
            want = []
            for at in range(len(data) - size + 1):
                value = read_pointer(data, at, size, endian) or 0
                if at + value in targets or (
                    value >= full >> 1 and at + value - full in targets
                ):
                    want.append(at)
            words = _relative_candidates(data, size, endian, targets, by_tops=False)
            assert words == want, (size, endian)
            if size >= 3:
                tops = _relative_candidates(data, size, endian, targets, by_tops=True)
                assert tops == want, (size, endian)
    assert 0x10 in _relative_candidates(data, 4, "big", targets)


def test_a_stop_is_heard_inside_one_step(monkeypatch):
    monkeypatch.setattr(engine, "TICK", 0.0)
    data = random.Random(5).randbytes(0x40000)
    calls: list[int] = []

    def progress(step: int, total: int) -> bool:
        calls.append(step)
        return len(calls) < 3

    discover(data, [0x20000], RELATIVE, sizes=(4,), progress=progress)
    # The first step was told twice more, from inside its scan, and stopped.
    assert calls == [0, 0, 0]
