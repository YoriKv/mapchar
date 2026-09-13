"""The run/literal packer the byte-oriented RLE schemes here share.

PackBits and the Konami RLE family are one encoder wearing two headers. Both walk
the input once, emit a run of equal bytes as its own packet the moment it is long
enough to pay for the header, and let everything else pile into a literal packet
that is flushed whenever a run interrupts it or the data ends. What differs is
arithmetic — how a header states its count, and how many bytes a packet may carry
— which is what this takes as parameters.

A scheme with a *terminator*, or a header before the packets, writes it around the
call: this produces the packet stream and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable


def pack_runs(
    data: bytes,
    out: bytearray,
    *,
    literal_header: Callable[[int], int],
    run_header: Callable[[int], int],
    max_packet: int,
    min_run: int,
    spill_pair_as_run: bool,
    run_limit: Callable[[int], int] | None = None,
) -> None:
    """Append ``data`` to ``out`` as run and literal packets.

    ``literal_header(count)`` and ``run_header(count)`` give the control byte for a
    packet of that many *output* bytes; a literal packet is that byte followed by
    its bytes, a run packet that byte followed by the value. ``max_packet`` caps
    both, and a run longer than it is written as several.

    ``min_run`` is the shortest run worth its own packet — below it the bytes ride
    along in the literal buffer at one byte each, with no new control byte to pay
    for. The same arithmetic leaves a 1- or 2-byte remainder after a long run has
    been cut into whole packets, and that tail spills back into the literal buffer
    the same way.

    ``spill_pair_as_run`` is the one place the two schemes genuinely differ. A pair
    with **no literal packet open** has nowhere free to ride: opening one to hold
    two bytes costs three where a run costs two, so PackBits emits the short run
    instead. The Konami encoder declines the trade, staying inside the subset every
    variant of that format decodes alike.

    ``run_limit(value)`` narrows ``max_packet`` for a run of one particular byte,
    for the scheme whose *terminator* a full-length run of that byte would spell —
    SMW's RLE1, where 128 copies of ``$FF`` write the ``$FF $FF`` that ends the
    stream. The bytes the cap leaves over re-enter the loop as an ordinary short
    run or literal, so the only cost is the packet the cap declined to fill.
    """
    literals = bytearray()

    def flush_literals() -> None:
        start = 0
        while start < len(literals):
            take = min(len(literals) - start, max_packet)
            out.append(literal_header(take))
            out.extend(literals[start : start + take])
            start += take
        literals.clear()

    i, n = 0, len(data)
    while i < n:
        value = data[i]
        run = 1
        while i + run < n and data[i + run] == value:
            run += 1
        i += run

        if run >= min_run:
            flush_literals()
            cap = max_packet if run_limit is None else min(max_packet, run_limit(value))
            while run >= min_run:
                take = min(run, cap)
                out.append(run_header(take))
                out.append(value)
                run -= take

        if run == 2 and spill_pair_as_run and not literals:
            out.append(run_header(2))
            out.append(value)
        else:
            literals += bytes([value]) * run

    flush_literals()
