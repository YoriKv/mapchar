"""The structure probe: is there a compressed structure here, and where is the next?

A scheme's decoder is the only thing that can answer either question, so both
are one decode attempt reported rather than raised — at almost every offset the
answer is no, and that is an answer, not a failure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mapchar.core.context import (
    KEY_COMPLETE,
    KEY_CONSUMED,
    KEY_DECOMPRESS_PARTIAL,
    PipelineContext,
)
from mapchar.pipeline.pipeline import bind_tables

__all__ = ["ScanResult", "Structure", "decompress_at", "find_next_structure"]


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
