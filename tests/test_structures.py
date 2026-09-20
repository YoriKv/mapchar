"""The structure probes over a buffer: where a signature says to look, what the
scheme's own decoder decides, and the tick a Stop reaches in on.

:mod:`mapchar.pipeline.structures` is what the Decompressed View's Scan, Jump
to Next and Structure to Block run, so a probe has to answer on a ROM's worth
of bytes in the time a byte scan takes, and has to tell a whole structure from
a stray magic and from a buffer that stops inside one.
"""

from __future__ import annotations

import time

from mapchar.core.context import PipelineContext
from mapchar.pipeline.structures import (
    find_next_structure,
    find_structures,
    scheme_at,
    signature_of,
)
from mapchar.plugins.base import Stage
from mapchar.plugins.builtins.compression import rnc


def _rnc2_noise(count: int) -> bytes:
    """Bytes no RNC magic can hide in: ascending, so ``52 4E 43`` never adjoin."""
    return (bytes(range(256)) * (count // 256 + 1))[:count]


def _stray_rnc2(packed_size: int = 20, *, payload: bool = True) -> bytes:
    """An ``RNC\\x02`` header no decode gets past: the packed CRC is nothing the
    bytes behind it give.

    ``payload`` lays ``packed_size`` bytes of ``$FF`` behind the header, so a
    decode reaches the CRC and fails it. Without them the buffer ends inside the
    stream instead, which is the other way a signature comes to nothing.
    """
    return (
        b"RNC\x02"
        + (100).to_bytes(4, "big")
        + packed_size.to_bytes(4, "big")
        + b"\x12\x34"  # unpacked CRC
        + b"\x56\x78"  # packed CRC, which the bytes behind it do not give
        + b"\x00\x01"
        + (b"\xff" * packed_size if payload else b"")
    )


# -- the whole-file walk -----------------------------------------------------


def test_find_structures_reads_the_edges_of_the_buffer(registry) -> None:
    """Three things the whole-file walk meets at a buffer's end.

    A signature in the last few bytes, a stream the file stops inside, and two
    streams with nothing between them: the first two are not structures, the
    walk still ends, and the third pair is found at both offsets.
    """
    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    first = rnc.compress(b"HELLO HELLO HELLO\x00" * 6, method=2)
    second = rnc.compress(b"WORLD WORLD WORLD\x00" * 4, method=2)

    assert find_structures(b"\xff" * 32 + b"RNC\x02", [rnc2]).found == []
    assert find_structures(b"\xff" * 16 + first[:-4], [rnc2]).found == []

    both = find_structures(first + second, [rnc2]).found
    assert [(f.offset, f.consumed) for f in both] == [
        (0, len(first)),
        (len(first), len(second)),
    ]


def test_find_structures_takes_every_signed_stream_and_no_stray_magic(registry):
    """The signature says where to look; the scheme's own decoder decides.

    Two streams in noise, with a third ``RNC\\x02`` whose header fails its packed
    CRC between them: the walk finds the two, at the offsets they were laid at
    and with the compressed extent each declares.
    """
    first = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    second = rnc.compress(b"WORLD WORLD WORLD\x00" * 6, method=2)
    head = _rnc2_noise(64)
    middle = _stray_rnc2() + _rnc2_noise(32)
    data = head + first + middle + second + _rnc2_noise(48)
    at_second = len(head) + len(first) + len(middle)

    result = find_structures(data, [registry.plugin(Stage.COMPRESSION, "rnc2")])
    assert not result.stopped
    assert [(f.offset, f.consumed) for f in result.found] == [
        (len(head), len(first)),
        (at_second, len(second)),
    ]
    assert [f.scheme_id for f in result.found] == ["rnc2", "rnc2"]
    assert [f.size for f in result.found] == [8 * 18, 6 * 18]
    # Nothing scores the payloads unless the caller asks.
    assert [f.score for f in result.found] == [0.0, 0.0]
    scored = find_structures(
        data,
        [registry.plugin(Stage.COMPRESSION, "rnc2")],
        score=lambda payload: len(payload) / 1000,
    )
    assert [f.score for f in scored.found] == [0.144, 0.108]


def test_find_structures_reports_the_scheme_and_can_be_stopped(registry, slotted):
    """Both walks answer the same tick, and each scheme it finds is named."""
    stream = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    data = _rnc2_noise(32) + stream

    stopped = find_structures(
        data, [registry.plugin(Stage.COMPRESSION, "rnc2")], on_tick=lambda _at: True
    )
    # Stopped at the first candidate, and what it had found by then is kept.
    assert stopped.stopped and [f.offset for f in stopped.found] == [32]
    # A scheme with no signature is walked a byte at a time, as the forward scan
    # walks it, and answers the same tick.
    doubler = slotted.plugin(Stage.COMPRESSION, "doubler")
    assert find_structures(
        b"\x00" * 512, [doubler], on_tick=lambda _at: True, progress_every=8
    ).stopped


# -- the forward scan --------------------------------------------------------


def test_find_next_structure_walks_the_schemes_it_is_given(registry) -> None:
    """The nearest structure of any of them, from the one walk.

    The forward scan takes the schemes to consider like the other two probes,
    so the Decompressed View's Scan is the picked scheme on a pick and every
    scheme that announces itself on automatic.
    """
    rnc1 = registry.plugin(Stage.COMPRESSION, "rnc1")
    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    one = rnc.compress(b"HELLO HELLO HELLO\x00" * 4, method=1)
    two = rnc.compress(b"WORLD WORLD WORLD\x00" * 4, method=2)
    data = b"\xff" * 8 + two + b"\xff" * 8 + one + b"\xff" * 8
    at_two, at_one = 8, 8 + len(two) + 8

    assert find_next_structure(data, [rnc1], 0).found == at_one
    assert find_next_structure(data, [rnc2], 0).found == at_two
    assert find_next_structure(data, [rnc1, rnc2], 0).found == at_two
    assert find_next_structure(data, [rnc1, rnc2], at_two + 1).found == at_one
    # Nothing to walk for is not a walk that found nothing: both end the same
    # way, and the caller is what tells them apart.
    assert find_next_structure(data, [], 0).found is None


def test_find_next_structure_looks_where_a_signature_says_to(registry) -> None:
    """A whole ROM's worth of bytes, walked in the time a byte scan takes.

    A strict decode at every offset of a 4 MB buffer is minutes of work, and
    the scan behind the Decompressed View's Scan button runs it on a keypress.
    A scheme that announces itself is only decoded where its signature sits, so
    the walk is `bytes.find` plus a decode per stray magic.
    """
    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    stream = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    stray = _stray_rnc2(50, payload=False)
    head = b"\xff" * 0x100000 + stray + b"\xff" * 0x100000 + stray
    data = head + b"\xff" * 0x200000 + stream + b"\xff" * 0x100
    at = len(head) + 0x200000

    started = time.perf_counter()
    result = find_next_structure(data, [rnc2], 0)
    assert result.found == at and not result.stopped
    assert time.perf_counter() - started < 1.0
    # And Stop still reaches in, on the first offset worth a decode rather than
    # after a megabyte of walking.
    seen: list[int] = []

    def tick(pos: int) -> bool:
        seen.append(pos)
        return True

    stopped = find_next_structure(data, [rnc2], 0, on_tick=tick)
    assert stopped.stopped and stopped.found is None
    # The first stray magic, decoded and stepped past: a tick a megabyte in
    # rather than after a megabyte of walking.
    assert seen == [0x100000 + 1]


def test_find_next_structure_mixes_schemes_that_announce_themselves_and_not(
    registry,
) -> None:
    """A signature jumps, no signature walks, and the nearest structure wins."""
    gba = registry.plugin(Stage.COMPRESSION, "gba_lz77")
    rnc2 = registry.plugin(Stage.COMPRESSION, "rnc2")
    packed = gba.compress(b"HELLO HELLO HELLO\x00" * 4, PipelineContext())
    stream = rnc.compress(b"WORLD WORLD WORLD\x00" * 4, method=2)
    # 0xFF starts no structure of either scheme, so only the two laid here do.
    data = b"\xff" * 64 + packed + b"\xff" * 64 + stream + b"\xff" * 64
    at_packed, at_stream = 64, 64 + len(packed) + 64

    assert find_next_structure(data, [gba], 0).found == at_packed
    assert find_next_structure(data, [rnc2], 0).found == at_stream
    assert find_next_structure(data, [rnc2, gba], 0).found == at_packed
    assert find_next_structure(data, [rnc2, gba], at_packed + 1).found == at_stream


def test_find_next_structure_can_be_stopped(slotted):
    plugin = slotted.plugin(Stage.COMPRESSION, "doubler")
    seen: list[int] = []

    def tick(at: int) -> bool:
        seen.append(at)
        return True

    result = find_next_structure(
        b"\x00" * 512, [plugin], 0, progress_every=8, on_tick=tick
    )
    assert result.stopped and result.found is None and result.end == 8
    assert seen == [8]


# -- one offset --------------------------------------------------------------


def test_scheme_at_arms_only_where_a_signature_reads_a_whole_structure(registry):
    stream = rnc.compress(b"HELLO HELLO HELLO\x00" * 8, method=2)
    data = _rnc2_noise(16) + stream + _stray_rnc2()
    schemes = [
        registry.plugin(Stage.COMPRESSION, id) for id in ("rnc1", "rnc2", "gba_lz77")
    ]
    found = scheme_at(data, schemes, 16)
    assert found is not None
    plugin, structure = found
    assert plugin.info.id == "rnc2" and structure.complete
    assert structure.consumed == len(stream)
    # One byte off the signature, and on a header that fails its CRC: nothing.
    assert scheme_at(data, schemes, 17) is None
    assert scheme_at(data, schemes, 16 + len(stream)) is None
    # A scheme that announces itself in no way is never armed by looking.
    assert signature_of(registry.plugin(Stage.COMPRESSION, "gba_lz77")) == b""
    assert signature_of(registry.plugin(Stage.COMPRESSION, "rnc1")) == b"RNC\x01"
