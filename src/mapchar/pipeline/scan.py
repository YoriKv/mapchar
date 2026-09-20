"""The structure probe: is there a compressed structure here, where is the next,
and where are they all?

A scheme's decoder is the only thing that can answer any of those, so each is
one decode attempt reported rather than raised — at almost every offset the
answer is no, and that is an answer, not a failure. A scheme that declares a
``signature`` says which offsets are worth asking about at all, which is what
:func:`scheme_at` and :func:`find_structures` lean on; the decode still decides.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from mapchar.core.context import (
    KEY_COMPLETE,
    KEY_CONSUMED,
    KEY_DECOMPRESS_PARTIAL,
    PipelineContext,
)
from mapchar.pipeline.pipeline import bind_tables

__all__ = [
    "FoundStructure",
    "ScanResult",
    "Structure",
    "StructureSearch",
    "decompress_at",
    "find_next_structure",
    "find_structures",
    "scheme_at",
    "signature_of",
]


@dataclass(frozen=True)
class Structure:
    """One compressed structure as a scheme read it."""

    data: bytes
    consumed: int
    """Compressed bytes the scheme took from its input."""
    complete: bool
    """Whether it reached its end marker rather than the edge of the window."""


def decompress_at(
    buffer: bytes,
    plugin: Any,
    offset: int,
    *,
    partial: bool = False,
    window: int = 0,
    ctx: PipelineContext | None = None,
) -> Structure | None:
    """``plugin``'s reading of the structure at ``offset``, or ``None``.

    ``None`` means the scheme rejected the bytes — the ordinary answer at almost
    every offset, which is why this reports rather than raises: a probe is not a
    load, and the caller is asking *whether* there is a structure here.

    ``partial`` asks for the prefix decoded so far when the input runs out
    mid-structure (:data:`KEY_DECOMPRESS_PARTIAL`), which is what a bounded
    preview wants: the window it was given is its own limit, not evidence that
    the data is bad. ``window`` caps how many compressed bytes are fed in;
    0 feeds the rest of the buffer.
    """
    ctx = PipelineContext() if ctx is None else ctx
    # Set both ways: a reused context carries the last probe's answer otherwise,
    # and a strict probe run after a preview would quietly stay lenient.
    ctx.set(KEY_DECOMPRESS_PARTIAL, partial)
    end = len(buffer) if window <= 0 else offset + window
    try:
        data = plugin.decompress(buffer[offset:end], ctx)
    except Exception:  # noqa: BLE001 - not a structure here
        return None
    if not data:
        return None
    return Structure(data, int(ctx.get(KEY_CONSUMED) or 0), bool(ctx.get(KEY_COMPLETE)))


@dataclass(frozen=True)
class ScanResult:
    """Where a forward structure scan ended (:func:`find_next_structure`).

    ``found`` is the hit offset or ``None``; ``end`` is where the scan stopped,
    which is where a caller lands when there was no hit; ``stopped`` is True
    when the caller aborted through its tick callback rather than reaching the
    end.
    """

    found: int | None
    end: int
    stopped: bool


def find_next_structure(
    buffer: bytes,
    plugin: Any,
    start: int,
    *,
    min_size: int = 16,
    window: int = 0,
    progress_every: int = 256,
    on_tick: Callable[[int], bool] | None = None,
) -> ScanResult:
    """The first offset at or after ``start`` where ``plugin`` reads a structure.

    Walks the buffer a byte at a time, trying a **strict** decode at each one; a
    hit is a *complete* structure of at least ``min_size`` bytes. Strict and
    complete because a best-effort partial decode succeeds on almost any bytes,
    which would make every offset a hit and the scan useless.

    Qt-free, and cancellable: every ``progress_every`` bytes ``on_tick(pos)`` is
    called if given, and returning True abandons the scan. That is the whole of
    how the toolbar's Stop button reaches in — the UI pumps its event loop in
    the callback and answers from whatever the user did.
    """
    # One context for the whole walk: it is what the scheme was bound against,
    # so every probe reads the tables that binding published.
    ctx = PipelineContext()
    bind_tables(plugin, buffer, ctx)
    pos = max(0, start)
    size = len(buffer)
    while pos < size:
        found = decompress_at(buffer, plugin, pos, window=window, ctx=ctx)
        if found is not None and found.complete and len(found.data) >= min_size:
            return ScanResult(pos, pos, False)
        pos += 1
        if on_tick is not None and pos % progress_every == 0 and on_tick(pos):
            return ScanResult(None, pos, True)
    return ScanResult(None, pos, False)


def signature_of(plugin: Any) -> bytes:
    """What ``plugin`` says its streams start with, or ``b""`` for nothing.

    Optional and probed, like ``bind_tree``: a scheme that declares no
    ``signature``, or declares something that is not bytes, simply announces
    itself in no way a comparison can find.
    """
    signature = getattr(plugin, "signature", None)
    if isinstance(signature, (bytes, bytearray)) and signature:
        return bytes(signature)
    return b""


def scheme_at(
    buffer: bytes, plugins: Iterable[Any], offset: int
) -> tuple[Any, Structure] | None:
    """The first of ``plugins`` whose signature sits at ``offset`` and whose
    decoder then reads a **complete** structure there, with that structure.

    What arms the Decompressed view by itself, so it runs wherever the view
    lands: the few bytes of the comparison come first and only a scheme that
    passes it is asked to decode. A signature is a claim about the bytes, not
    proof — RNC's magic is three letters — so the decode is what decides, and
    strictly, since a partial read succeeds on almost anything.
    """
    for plugin in plugins:
        signature = signature_of(plugin)
        if not signature or buffer[offset : offset + len(signature)] != signature:
            continue
        ctx = PipelineContext()
        bind_tables(plugin, buffer, ctx)
        found = decompress_at(buffer, plugin, offset, ctx=ctx)
        if found is not None and found.complete:
            return plugin, found
    return None


@dataclass(frozen=True)
class FoundStructure:
    """One structure a whole-buffer walk found (:func:`find_structures`)."""

    offset: int
    scheme_id: str
    consumed: int
    """Compressed bytes the structure takes."""
    size: int
    """Bytes it decompresses to."""
    score: float = 0.0
    """How text-like the payload is, from the caller's ``score``; 0 without one."""


@dataclass(frozen=True)
class StructureSearch:
    """What :func:`find_structures` found, and whether it ran to the end."""

    found: list[FoundStructure]
    stopped: bool


def find_structures(
    buffer: bytes,
    plugins: Iterable[Any],
    *,
    min_size: int = 16,
    score: Callable[[bytes], float] | None = None,
    on_tick: Callable[[int], bool] | None = None,
    progress_every: int = 256,
) -> StructureSearch:
    """Every structure the schemes in ``plugins`` read in ``buffer``, by offset.

    A scheme that declares a ``signature`` (:func:`signature_of`) is searched
    for it, and only the offsets carrying it are decoded — which is what makes
    a whole ROM cheap, since the search itself is a byte scan and a scheme like
    RNC throws out stray magic on a checksum before unpacking anything. A
    scheme that declares none is walked a byte at a time, as
    :func:`find_next_structure` walks it, and ``min_size`` then keeps a decode
    of a few plausible bytes from counting; a signature hit is taken whatever
    its size, because the scheme recognised the bytes rather than merely
    surviving them.

    ``score`` is asked about each payload, for the text-likeness a caller shows
    beside the structure. ``on_tick(pos)`` is :func:`find_next_structure`'s
    cancel callback, answering True to abandon the search — what was found by
    then is kept and ``stopped`` says the walk is not the whole story.
    """
    found: list[FoundStructure] = []
    stopped = False
    for plugin in plugins:
        # One context per scheme, for the same reason the forward scan keeps
        # one: it is what this scheme was bound against.
        ctx = PipelineContext()
        bind_tables(plugin, buffer, ctx)
        signature = signature_of(plugin)
        at, size, next_tick = 0, len(buffer), progress_every
        while at < size:
            if signature:
                at = buffer.find(signature, at)
                if at < 0:
                    break
            structure = decompress_at(buffer, plugin, at, ctx=ctx)
            hit = structure is not None and structure.complete
            if hit and not signature:
                hit = len(structure.data) >= min_size
            if hit:
                found.append(
                    FoundStructure(
                        at,
                        plugin.info.id,
                        structure.consumed,
                        len(structure.data),
                        score(structure.data) if score is not None else 0.0,
                    )
                )
            # Past a structure rather than into it: the bytes it covers are
            # its own, and a decoder that consumed nothing still has to move.
            at += structure.consumed if hit and structure.consumed else 1
            if on_tick is not None and (signature or at >= next_tick):
                next_tick = at + progress_every
                if on_tick(at):
                    stopped = True
                    break
        if stopped:
            break
    found.sort(key=lambda f: (f.offset, f.scheme_id))
    return StructureSearch(found, stopped)
