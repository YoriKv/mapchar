"""Bit layouts (:mod:`mapchar.capture.bitlayout`): an alignment that runs out
of its budget says so, the remembered failures change no answer, and several
sightings are merged one at a time to the same layouts as every combination."""

from __future__ import annotations

import itertools
import random

from mapchar.capture import bitlayout
from mapchar.capture.bitlayout import Decision, Layout, _Outputs, align, combine

ROM = bytes(random.Random(3).randrange(4) for _ in range(256))


def _case(rng: random.Random):
    ncodes = rng.randint(3, 7)
    entries = [(rng.randrange(0, 40), rng.randint(1, 3)) for _ in range(ncodes)]
    script = [rng.randrange(ncodes + 2) for _ in range(rng.randint(6, 22))]
    outs = []
    for c in script:
        if c < ncodes:
            a, k = entries[c]
            outs += list(range(a, a + k))
    if outs and rng.random() < 0.5:
        outs[rng.randrange(len(outs))] = rng.randrange(40)
    return script, outs


def test_running_out_of_budget_is_counted_apart():
    # A run cut many ways before each cut fails: the codes recur, and no
    # meaning of theirs spells the run twice with another output after each.
    run = list(range(100, 116))
    outs = run + [7] + run + [8]
    codes = [0, 1, 2, 3, 0, 1, 2, 3]
    o = _Outputs(outs, ROM.__getitem__)
    assert align(codes, o, budget=10**7) is None
    assert o.exhausted == 0  # outside the model: nothing ran out
    o = _Outputs(outs, ROM.__getitem__)
    assert align(codes, o, budget=5) is None
    assert o.exhausted == 1


def test_fits_says_how_many_searches_ran_out(monkeypatch):
    run = list(range(100, 116))
    outs = run + [7] + run + [8]
    data = bytes([0x12, 0x34, 0x56, 0x78, 0x9A])
    found = bitlayout.fits(outs, data, ROM.__getitem__)
    assert isinstance(found, list) and found.exhausted == 0
    monkeypatch.setattr(align, "__defaults__", (0,))
    starved = bitlayout.fits(outs, data, ROM.__getitem__)
    assert starved.exhausted > 0 and len(starved) < len(found)


def test_remembered_failures_change_no_answer(monkeypatch):
    rng = random.Random(11)
    cases = [_case(rng) for _ in range(400)]
    cases = [(c, o) for c, o in cases if o]

    def answers():
        return [
            align(list(c), _Outputs(o, ROM.__getitem__), budget=10**6) is not None
            for c, o in cases
        ]

    remembered = answers()
    monkeypatch.setattr(bitlayout, "MEMO", 10**12)
    assert answers() == remembered
    assert any(remembered) and not all(remembered)


def _layouts(rng: random.Random, n: int) -> list[Layout]:
    out = []
    for _ in range(n):
        key = rng.choice([("msb", 5, 0), ("msb", 5, 1), ("lsb", 6, 0)])
        table = {
            c: (rng.randrange(6), 1) if rng.random() < 0.8 else bitlayout.SILENT
            for c in rng.sample(range(8), rng.randint(1, 4))
        }
        out.append(Layout(*key, rng.randrange(24), table))
    return out


def _every_combination(per: list[list[Layout]]) -> list:
    alive = []
    for key in {lay.key for lays in per for lay in lays}:
        slots = [[lay for lay in lays if lay.key == key] for lays in per]
        for combo in itertools.product(*slots):
            t = bitlayout.merge([lay.table for lay in combo])
            if t is not None:
                alive.append((key, [lay.start for lay in combo], t))
    return alive


def test_merging_a_sighting_at_a_time_is_every_combination():
    rng = random.Random(5)
    for _ in range(200):
        per = [_layouts(rng, rng.randint(1, 6)) for _ in range(rng.randint(1, 4))]
        got = combine(per).alive
        want = _every_combination(per)

        def norm(alive):
            return sorted(
                repr((k, s, sorted(t.items(), key=repr))) for k, s, t in alive
            )

        assert norm(got) == norm(want)


def test_layouts_that_differ_only_in_unused_escapes_are_one():
    table = {1: (10, 1), 2: (11, 2)}
    a = [Layout("msb", 5, 0, 3, table), Layout("msb", 5, 2, 3, table)]
    b = [Layout("msb", 5, 0, 0, {3: (20, 1)}), Layout("msb", 5, 2, 0, {3: (20, 1)})]
    d = combine([a, b])
    assert d.keys == [("msb", 5, 0), ("msb", 5, 2)]
    assert d.decided[0] == ("msb", 5, 0) and d.decided[1] == [3, 0]
    # Two tables left: nothing is decided until another sighting tells.
    other = Layout("msb", 5, 2, 3, {1: (10, 1), 2: (12, 1)})
    assert combine([a[:1] + [other], b]).decided is None
    assert Decision([]).decided is None
